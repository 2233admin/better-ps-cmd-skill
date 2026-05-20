"""Order state machine + SQLite WAL persistence (XAR-481).

Wraps the per-adapter reconciliation modules in a formal 11-state order state
machine with an append-only event log persisted to SQLite (WAL mode).

State graph (see ``docs/order-state-machine.md`` for mermaid):

    draft -> submitted -> {accepted | rejected}
    accepted -> {partial_filled | filled | cancel_pending | expired}
    partial_filled -> {filled | cancel_pending | expired}
    cancel_pending -> {cancelled | filled}    # cancel race
    {filled | cancelled | rejected | expired} -> reconciled

Plus the FDI auxiliary state ``quarantined``: any order stuck in a non-terminal
state for more than ``stale_after_ns`` (default 60s) is force-moved to
``quarantined`` by ``ReconciliationStateMachine.tick``. Reaching ``quarantined``
is terminal -- recovery is operator-driven.

Design constraints (FSC v3 handoff A2):

* No ``python-statemachine`` / ``transitions`` library -- a dict-of-transitions
  plus an ``if`` chain is enough.
* Timestamps are UTC ns ints (``time.time_ns()``), never ``datetime``.
* SQLite append latency target < 10 ms / op (WAL + single-row INSERT).
* Coexists with the legacy JSONL ``recon_state`` module -- this is a separate
  module, no breakage to existing callers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


class OrderState(str, Enum):
    """Order lifecycle states.

    Values are stable strings -- they go into SQLite text columns and JSONL,
    so do NOT rename without a migration.
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    RECONCILED = "reconciled"
    QUARANTINED = "quarantined"


# Transitions allowed by the handoff state graph. Each entry maps
# (from_state, to_state) -> short human-readable reason tag. Anything not in
# this dict raises IllegalTransition.
_TRANSITIONS: dict[tuple[OrderState, OrderState], str] = {
    # initial submission
    (OrderState.DRAFT, OrderState.SUBMITTED): "submit",
    # broker ack / reject after submit
    (OrderState.SUBMITTED, OrderState.ACCEPTED): "broker_accept",
    (OrderState.SUBMITTED, OrderState.REJECTED): "broker_reject",
    # fills + lifecycle from accepted
    (OrderState.ACCEPTED, OrderState.PARTIAL_FILLED): "partial_fill",
    (OrderState.ACCEPTED, OrderState.FILLED): "full_fill",
    (OrderState.ACCEPTED, OrderState.CANCEL_PENDING): "cancel_request",
    (OrderState.ACCEPTED, OrderState.EXPIRED): "expire",
    # fills + lifecycle from partial_filled
    (OrderState.PARTIAL_FILLED, OrderState.FILLED): "full_fill",
    (OrderState.PARTIAL_FILLED, OrderState.CANCEL_PENDING): "cancel_request",
    (OrderState.PARTIAL_FILLED, OrderState.EXPIRED): "expire",
    # cancel race -- broker may fill before our cancel lands
    (OrderState.CANCEL_PENDING, OrderState.CANCELLED): "cancel_confirm",
    (OrderState.CANCEL_PENDING, OrderState.FILLED): "cancel_race_filled",
    # reconcile sweep: any terminal-but-not-reconciled state can converge
    (OrderState.FILLED, OrderState.RECONCILED): "reconcile",
    (OrderState.CANCELLED, OrderState.RECONCILED): "reconcile",
    (OrderState.REJECTED, OrderState.RECONCILED): "reconcile",
    (OrderState.EXPIRED, OrderState.RECONCILED): "reconcile",
}


# Non-terminal states are anything the FDI tick may force-quarantine when
# stale. Reconciled + quarantined are the two true terminal states; the four
# "outcome" states (filled / cancelled / rejected / expired) are intermediate
# until they reconcile and so still count as non-terminal for FDI purposes.
TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {OrderState.RECONCILED, OrderState.QUARANTINED}
)


_QUARANTINE_TRANSITION_REASON = "fdi_stale"


def is_legal_transition(from_state: OrderState, to_state: OrderState) -> bool:
    """Pure predicate: is this transition in the allow-list?

    Quarantine is allowed from any non-terminal state -- it is the FDI escape
    hatch and is never in ``_TRANSITIONS``.
    """

    if to_state is OrderState.QUARANTINED and from_state not in TERMINAL_STATES:
        return True
    return (from_state, to_state) in _TRANSITIONS


class IllegalTransition(ValueError):
    """Raised when a transition is not in ``_TRANSITIONS`` and is not an FDI quarantine."""


@dataclass
class OrderEvent:
    """A single transition record. Timestamps are UTC ns ints."""

    ts: int
    order_id: str
    from_state: OrderState
    to_state: OrderState
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_row(self) -> tuple[str, int, str, str, str, str]:
        """Map to the ``order_events`` SQLite row layout."""

        return (
            self.order_id,
            self.ts,
            self.from_state.value,
            self.to_state.value,
            json.dumps(self.payload, ensure_ascii=True, sort_keys=True),
            self.source,
        )

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "OrderEvent":
        # row layout: (id, order_id, ts, from_state, to_state, payload_json, source)
        return cls(
            ts=int(row[2]),
            order_id=str(row[1]),
            from_state=OrderState(row[3]),
            to_state=OrderState(row[4]),
            payload=json.loads(row[5]) if row[5] else {},
            source=str(row[6]) if row[6] else "",
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["from_state"] = self.from_state.value
        payload["to_state"] = self.to_state.value
        return payload


_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_order_events_order_id ON order_events(order_id);
CREATE INDEX IF NOT EXISTS idx_order_events_ts ON order_events(ts);
"""


DEFAULT_STALE_AFTER_NS = 60 * 1_000_000_000  # 60s in nanoseconds


class ReconciliationStateMachine:
    """Append-only event log + snapshot per order_id.

    Threading: a single ``threading.RLock`` guards the in-memory snapshot map
    and the SQLite connection. The connection is opened once with WAL mode
    and reused -- SQLite write latency stays well under 10 ms / op for a
    single-row INSERT in WAL.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        stale_after_ns: int = DEFAULT_STALE_AFTER_NS,
        clock_ns: Any = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._stale_after_ns = int(stale_after_ns)
        self._clock_ns = clock_ns or time.time_ns
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_SCHEMA)
        # snapshot: order_id -> (current_state, last_event_ts)
        self._snapshot: dict[str, tuple[OrderState, int]] = {}
        self._replay()

    # ------------------------------------------------------------------
    # core API
    # ------------------------------------------------------------------

    def state(self, order_id: str) -> OrderState | None:
        """Current state for ``order_id``, or ``None`` if never seen."""

        with self._lock:
            entry = self._snapshot.get(order_id)
            return entry[0] if entry else None

    def last_ts(self, order_id: str) -> int | None:
        with self._lock:
            entry = self._snapshot.get(order_id)
            return entry[1] if entry else None

    def submit(
        self,
        order_id: str,
        to_state: OrderState,
        *,
        payload: dict[str, Any] | None = None,
        source: str = "",
        ts: int | None = None,
    ) -> OrderEvent:
        """Record a transition. Raises ``IllegalTransition`` if not allowed.

        New orders (no prior state) may only transition from ``DRAFT``. Pass
        ``to_state=OrderState.DRAFT`` to register an order before submission
        (the synthetic DRAFT -> DRAFT self-event is rejected).
        """

        payload = dict(payload or {})
        ts = int(ts if ts is not None else self._clock_ns())
        with self._lock:
            current = self._snapshot.get(order_id)
            if current is None:
                # bootstrap: only allow DRAFT or DRAFT -> X via implicit draft start
                if to_state is OrderState.DRAFT:
                    from_state = OrderState.DRAFT
                else:
                    from_state = OrderState.DRAFT
                    if not is_legal_transition(from_state, to_state):
                        raise IllegalTransition(
                            f"cannot bootstrap order {order_id!r} directly into {to_state.value}"
                        )
            else:
                from_state, _ = current
                if from_state is to_state:
                    # idempotent no-op -- do not write
                    return OrderEvent(
                        ts=ts,
                        order_id=order_id,
                        from_state=from_state,
                        to_state=to_state,
                        payload=payload,
                        source=source,
                    )
                if not is_legal_transition(from_state, to_state):
                    raise IllegalTransition(
                        f"order {order_id!r}: {from_state.value} -> {to_state.value} not allowed"
                    )

            event = OrderEvent(
                ts=ts,
                order_id=order_id,
                from_state=from_state,
                to_state=to_state,
                payload=payload,
                source=source,
            )
            self._append(event)
            self._snapshot[order_id] = (to_state, ts)
            return event

    def tick(self, *, now_ns: int | None = None) -> tuple[OrderEvent, ...]:
        """FDI sweep: quarantine any non-terminal order stuck > ``stale_after_ns``.

        Returns the list of quarantine events emitted (possibly empty). Idempotent
        once an order is quarantined (terminal, so no re-fire).
        """

        now = int(now_ns if now_ns is not None else self._clock_ns())
        events: list[OrderEvent] = []
        with self._lock:
            for order_id, (current_state, last_ts) in list(self._snapshot.items()):
                if current_state in TERMINAL_STATES:
                    continue
                if (now - last_ts) <= self._stale_after_ns:
                    continue
                event = OrderEvent(
                    ts=now,
                    order_id=order_id,
                    from_state=current_state,
                    to_state=OrderState.QUARANTINED,
                    payload={
                        "stale_for_ns": now - last_ts,
                        "stale_after_ns": self._stale_after_ns,
                    },
                    source=_QUARANTINE_TRANSITION_REASON,
                )
                self._append(event)
                self._snapshot[order_id] = (OrderState.QUARANTINED, now)
                events.append(event)
        return tuple(events)

    def history(self, order_id: str) -> tuple[OrderEvent, ...]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, order_id, ts, from_state, to_state, payload_json, source "
                "FROM order_events WHERE order_id = ? ORDER BY id ASC",
                (order_id,),
            )
            rows = cursor.fetchall()
        return tuple(OrderEvent.from_row(row) for row in rows)

    def all_orders(self) -> dict[str, OrderState]:
        with self._lock:
            return {oid: state for oid, (state, _) in self._snapshot.items()}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # context manager sugar
    # ------------------------------------------------------------------

    def __enter__(self) -> "ReconciliationStateMachine":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _append(self, event: OrderEvent) -> None:
        row = event.to_row()
        self._conn.execute(
            "INSERT INTO order_events (order_id, ts, from_state, to_state, payload_json, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )

    def _replay(self) -> None:
        cursor = self._conn.execute(
            "SELECT order_id, ts, to_state FROM order_events ORDER BY id ASC"
        )
        for order_id, ts, to_state in cursor:
            self._snapshot[str(order_id)] = (OrderState(to_state), int(ts))


def legal_transitions() -> Iterable[tuple[OrderState, OrderState, str]]:
    """Iterate ``(from, to, reason)`` for the static allow-list.

    Used by tests + docs to assert that every mermaid arrow exists in
    ``_TRANSITIONS``.
    """

    for (src, dst), reason in _TRANSITIONS.items():
        yield src, dst, reason
