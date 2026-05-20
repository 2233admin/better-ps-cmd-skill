"""Unit tests for trading.state_machine (XAR-481).

Covers:
* 5-state happy path (draft -> submitted -> accepted -> filled -> reconciled)
* All declared transitions in ``_TRANSITIONS`` are reachable
* Mermaid arrows in docs == _TRANSITIONS keys (no drift)
* Illegal transitions raise ``IllegalTransition``
* SQLite persistence: events survive restart, snapshot rebuilt via replay
* Two race conditions on cancel_pending:
    - cancel_pending -> filled (broker filled before our cancel landed)
    - cancel_pending -> cancelled (clean cancel ack)
* FDI tick: non-terminal + stale -> quarantined; terminal untouched
* Idempotent no-op on self-state submit
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.trading.state_machine import (
    DEFAULT_STALE_AFTER_NS,
    IllegalTransition,
    OrderEvent,
    OrderState,
    ReconciliationStateMachine,
    TERMINAL_STATES,
    _TRANSITIONS,
    is_legal_transition,
    legal_transitions,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    """Monotonic ns clock with manual control for FDI tests."""

    def __init__(self, start_ns: int = 1_700_000_000_000_000_000) -> None:
        self.now = int(start_ns)

    def __call__(self) -> int:
        return self.now

    def advance(self, delta_ns: int) -> None:
        self.now += int(delta_ns)


def _sm(tmp_path: Path, *, stale_after_ns: int = DEFAULT_STALE_AFTER_NS) -> tuple[ReconciliationStateMachine, _FakeClock]:
    clock = _FakeClock()
    sm = ReconciliationStateMachine(
        tmp_path / "orders.db",
        stale_after_ns=stale_after_ns,
        clock_ns=clock,
    )
    return sm, clock


# ---------------------------------------------------------------------------
# transition allow-list integrity
# ---------------------------------------------------------------------------


def test_transitions_contain_every_handoff_arrow():
    """The mermaid arrows in the FSC v3 handoff spec must each exist in _TRANSITIONS.

    If you change the diagram, update this list -- it is the contract.
    """

    expected_arrows = {
        (OrderState.DRAFT, OrderState.SUBMITTED),
        (OrderState.SUBMITTED, OrderState.ACCEPTED),
        (OrderState.SUBMITTED, OrderState.REJECTED),
        (OrderState.ACCEPTED, OrderState.PARTIAL_FILLED),
        (OrderState.ACCEPTED, OrderState.FILLED),
        (OrderState.ACCEPTED, OrderState.CANCEL_PENDING),
        (OrderState.ACCEPTED, OrderState.EXPIRED),
        (OrderState.PARTIAL_FILLED, OrderState.FILLED),
        (OrderState.PARTIAL_FILLED, OrderState.CANCEL_PENDING),
        (OrderState.PARTIAL_FILLED, OrderState.EXPIRED),
        (OrderState.CANCEL_PENDING, OrderState.CANCELLED),
        (OrderState.CANCEL_PENDING, OrderState.FILLED),
        (OrderState.FILLED, OrderState.RECONCILED),
        (OrderState.CANCELLED, OrderState.RECONCILED),
        (OrderState.REJECTED, OrderState.RECONCILED),
        (OrderState.EXPIRED, OrderState.RECONCILED),
    }
    actual_arrows = set(_TRANSITIONS.keys())
    assert actual_arrows == expected_arrows, (
        f"missing arrows: {expected_arrows - actual_arrows}; "
        f"extra arrows: {actual_arrows - expected_arrows}"
    )


def test_mermaid_doc_arrows_match_transitions():
    """Each arrow in docs/order-state-machine.md must appear in _TRANSITIONS.

    Prevents diagram-vs-code drift -- if the mermaid diagram adds an arrow
    that has no code path, this test fails.
    """

    doc = Path(__file__).resolve().parents[3] / "docs" / "order-state-machine.md"
    text = doc.read_text(encoding="utf-8")
    arrow_pattern = re.compile(r"^\s*([a-z_]+)\s*-->\s*(?:\|[^|]+\|\s*)?([a-z_]+)\s*$", re.MULTILINE)
    parsed = set()
    for match in arrow_pattern.finditer(text):
        try:
            src = OrderState(match.group(1))
            dst = OrderState(match.group(2))
        except ValueError:
            continue  # ignore non-state labels in the diagram
        # FDI quarantine edges from the diagram are intentionally not in
        # _TRANSITIONS (they are handled by tick()). Skip those.
        if dst is OrderState.QUARANTINED:
            continue
        parsed.add((src, dst))
    assert parsed, "mermaid doc parser found no arrows -- diagram regression"
    missing_in_code = parsed - set(_TRANSITIONS.keys())
    assert not missing_in_code, f"mermaid arrows missing in _TRANSITIONS: {missing_in_code}"


def test_is_legal_transition_quarantine_from_non_terminal():
    assert is_legal_transition(OrderState.ACCEPTED, OrderState.QUARANTINED)
    assert is_legal_transition(OrderState.PARTIAL_FILLED, OrderState.QUARANTINED)
    # quarantine from terminal is NOT legal -- terminal states are absorbing
    assert not is_legal_transition(OrderState.RECONCILED, OrderState.QUARANTINED)
    assert not is_legal_transition(OrderState.QUARANTINED, OrderState.QUARANTINED)


def test_legal_transitions_iter_returns_all_entries():
    entries = list(legal_transitions())
    assert len(entries) == len(_TRANSITIONS)
    for src, dst, reason in entries:
        assert isinstance(src, OrderState)
        assert isinstance(dst, OrderState)
        assert reason  # non-empty


# ---------------------------------------------------------------------------
# happy path + persistence
# ---------------------------------------------------------------------------


def test_happy_path_5_states(tmp_path: Path) -> None:
    sm, clock = _sm(tmp_path)
    sm.submit("o1", OrderState.DRAFT, source="user")
    clock.advance(1_000_000)
    sm.submit("o1", OrderState.SUBMITTED, source="executor")
    clock.advance(1_000_000)
    sm.submit("o1", OrderState.ACCEPTED, source="broker")
    clock.advance(1_000_000)
    sm.submit("o1", OrderState.FILLED, payload={"qty": 100, "px": 12.5}, source="broker")
    clock.advance(1_000_000)
    sm.submit("o1", OrderState.RECONCILED, source="recon")

    assert sm.state("o1") == OrderState.RECONCILED
    history = sm.history("o1")
    # DRAFT bootstrap is a no-op self-loop and is not written -> 4 real transitions
    transitions = [(e.from_state, e.to_state) for e in history]
    assert (OrderState.DRAFT, OrderState.SUBMITTED) in transitions
    assert (OrderState.SUBMITTED, OrderState.ACCEPTED) in transitions
    assert (OrderState.ACCEPTED, OrderState.FILLED) in transitions
    assert (OrderState.FILLED, OrderState.RECONCILED) in transitions


def test_partial_fill_then_full_fill(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.ACCEPTED)
    sm.submit("o1", OrderState.PARTIAL_FILLED, payload={"filled_qty": 30, "total_qty": 100})
    sm.submit("o1", OrderState.FILLED, payload={"filled_qty": 100, "total_qty": 100})
    sm.submit("o1", OrderState.RECONCILED)
    assert sm.state("o1") == OrderState.RECONCILED


def test_rejected_path(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.REJECTED, payload={"reason": "insufficient_margin"})
    sm.submit("o1", OrderState.RECONCILED)
    assert sm.state("o1") == OrderState.RECONCILED


def test_expired_path(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.ACCEPTED)
    sm.submit("o1", OrderState.EXPIRED)
    sm.submit("o1", OrderState.RECONCILED)
    assert sm.state("o1") == OrderState.RECONCILED


# ---------------------------------------------------------------------------
# illegal transitions
# ---------------------------------------------------------------------------


def test_illegal_skip_submitted_to_filled_raises(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    with pytest.raises(IllegalTransition):
        sm.submit("o1", OrderState.FILLED)


def test_illegal_reverse_transition_raises(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.ACCEPTED)
    with pytest.raises(IllegalTransition):
        sm.submit("o1", OrderState.SUBMITTED)


def test_illegal_bootstrap_into_random_state_raises(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    with pytest.raises(IllegalTransition):
        sm.submit("o-new", OrderState.FILLED)


def test_terminal_reconciled_blocks_further_transitions(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.REJECTED)
    sm.submit("o1", OrderState.RECONCILED)
    with pytest.raises(IllegalTransition):
        sm.submit("o1", OrderState.SUBMITTED)


def test_idempotent_self_transition_no_write(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    before = len(sm.history("o1"))
    sm.submit("o1", OrderState.SUBMITTED)
    after = len(sm.history("o1"))
    assert before == after  # no duplicate row


# ---------------------------------------------------------------------------
# cancel race conditions (the two required by the spec)
# ---------------------------------------------------------------------------


def test_cancel_race_clean_cancel_confirm(tmp_path: Path) -> None:
    """cancel_pending -> cancelled when broker confirms cancel before any fill."""

    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.ACCEPTED)
    sm.submit("o1", OrderState.CANCEL_PENDING, source="user_cancel")
    sm.submit("o1", OrderState.CANCELLED, source="broker_ack")
    sm.submit("o1", OrderState.RECONCILED)
    assert sm.state("o1") == OrderState.RECONCILED


def test_cancel_race_broker_fills_before_cancel(tmp_path: Path) -> None:
    """cancel_pending -> filled: broker filled the order before our cancel reached it."""

    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.ACCEPTED)
    sm.submit("o1", OrderState.CANCEL_PENDING, source="user_cancel")
    # broker reports a fill -- our cancel lost the race
    sm.submit(
        "o1",
        OrderState.FILLED,
        payload={"race": "broker_filled_before_cancel"},
        source="broker",
    )
    sm.submit("o1", OrderState.RECONCILED)
    history = sm.history("o1")
    transitions = [(e.from_state, e.to_state) for e in history]
    assert (OrderState.CANCEL_PENDING, OrderState.FILLED) in transitions


def test_cancel_pending_cannot_jump_to_cancelled_skipping_accepted(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    with pytest.raises(IllegalTransition):
        sm.submit("o1", OrderState.CANCEL_PENDING)


# ---------------------------------------------------------------------------
# FDI: stale orders auto-quarantine
# ---------------------------------------------------------------------------


def test_fdi_tick_quarantines_stale_non_terminal(tmp_path: Path) -> None:
    sm, clock = _sm(tmp_path, stale_after_ns=60 * 1_000_000_000)
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.ACCEPTED)
    # advance 61s -> stale
    clock.advance(61 * 1_000_000_000)
    events = sm.tick()
    assert len(events) == 1
    assert events[0].order_id == "o1"
    assert events[0].to_state is OrderState.QUARANTINED
    assert events[0].payload["stale_for_ns"] >= 61 * 1_000_000_000
    assert sm.state("o1") is OrderState.QUARANTINED


def test_fdi_tick_idempotent(tmp_path: Path) -> None:
    sm, clock = _sm(tmp_path, stale_after_ns=10 * 1_000_000_000)
    sm.submit("o1", OrderState.SUBMITTED)
    clock.advance(11 * 1_000_000_000)
    first = sm.tick()
    second = sm.tick()
    assert len(first) == 1
    assert second == ()  # quarantined is terminal, no re-fire


def test_fdi_tick_skips_terminal(tmp_path: Path) -> None:
    sm, clock = _sm(tmp_path, stale_after_ns=1)  # very tight
    sm.submit("o1", OrderState.SUBMITTED)
    sm.submit("o1", OrderState.REJECTED)
    sm.submit("o1", OrderState.RECONCILED)
    clock.advance(10 * 1_000_000_000)
    events = sm.tick()
    assert events == ()
    assert sm.state("o1") is OrderState.RECONCILED


def test_fdi_tick_skips_fresh_non_terminal(tmp_path: Path) -> None:
    sm, clock = _sm(tmp_path, stale_after_ns=60 * 1_000_000_000)
    sm.submit("o1", OrderState.SUBMITTED)
    clock.advance(1_000_000_000)  # 1s -- under threshold
    assert sm.tick() == ()


def test_fdi_quarantine_is_terminal_blocks_further_writes(tmp_path: Path) -> None:
    sm, clock = _sm(tmp_path, stale_after_ns=1)
    sm.submit("o1", OrderState.SUBMITTED)
    clock.advance(10 * 1_000_000_000)
    sm.tick()
    with pytest.raises(IllegalTransition):
        sm.submit("o1", OrderState.ACCEPTED)


# ---------------------------------------------------------------------------
# persistence + replay
# ---------------------------------------------------------------------------


def test_replay_restores_snapshot_after_reopen(tmp_path: Path) -> None:
    db = tmp_path / "orders.db"
    sm1 = ReconciliationStateMachine(db)
    sm1.submit("o1", OrderState.SUBMITTED)
    sm1.submit("o1", OrderState.ACCEPTED)
    sm1.submit("o2", OrderState.SUBMITTED)
    sm1.submit("o2", OrderState.REJECTED)
    sm1.close()

    sm2 = ReconciliationStateMachine(db)
    try:
        assert sm2.state("o1") is OrderState.ACCEPTED
        assert sm2.state("o2") is OrderState.REJECTED
        all_orders = sm2.all_orders()
        assert all_orders == {"o1": OrderState.ACCEPTED, "o2": OrderState.REJECTED}
    finally:
        sm2.close()


def test_wal_mode_enabled(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    cursor = sm._conn.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    assert str(mode).lower() == "wal"
    sm.close()


def test_history_returns_ordered_events(tmp_path: Path) -> None:
    sm, clock = _sm(tmp_path)
    sm.submit("o1", OrderState.SUBMITTED)
    clock.advance(1)
    sm.submit("o1", OrderState.ACCEPTED)
    clock.advance(1)
    sm.submit("o1", OrderState.FILLED)
    history = sm.history("o1")
    assert [e.to_state for e in history] == [
        OrderState.SUBMITTED,
        OrderState.ACCEPTED,
        OrderState.FILLED,
    ]
    timestamps = [e.ts for e in history]
    assert timestamps == sorted(timestamps)


def test_unknown_order_returns_none(tmp_path: Path) -> None:
    sm, _ = _sm(tmp_path)
    assert sm.state("does-not-exist") is None
    assert sm.last_ts("does-not-exist") is None
    assert sm.history("does-not-exist") == ()


def test_terminal_states_set_is_correct() -> None:
    assert TERMINAL_STATES == frozenset({OrderState.RECONCILED, OrderState.QUARANTINED})


def test_context_manager_closes_connection(tmp_path: Path) -> None:
    db = tmp_path / "orders.db"
    with ReconciliationStateMachine(db) as sm:
        sm.submit("o1", OrderState.SUBMITTED)
    # reopening should still work -- previous connection cleanly closed
    sm2 = ReconciliationStateMachine(db)
    try:
        assert sm2.state("o1") is OrderState.SUBMITTED
    finally:
        sm2.close()


# ---------------------------------------------------------------------------
# OrderEvent serialization
# ---------------------------------------------------------------------------


def test_order_event_to_row_and_back(tmp_path: Path) -> None:
    event = OrderEvent(
        ts=1_700_000_000_000_000_000,
        order_id="o1",
        from_state=OrderState.ACCEPTED,
        to_state=OrderState.FILLED,
        payload={"qty": 100, "px": 12.5},
        source="broker",
    )
    row = event.to_row()
    assert row[0] == "o1"
    assert row[1] == 1_700_000_000_000_000_000
    assert row[2] == "accepted"
    assert row[3] == "filled"
    # simulate SQLite row layout: id is prepended
    db_row = (42, *row)
    restored = OrderEvent.from_row(db_row)
    assert restored.from_state is OrderState.ACCEPTED
    assert restored.to_state is OrderState.FILLED
    assert restored.payload == {"qty": 100, "px": 12.5}
    assert restored.source == "broker"


def test_order_event_to_dict_has_string_states() -> None:
    event = OrderEvent(
        ts=1,
        order_id="o1",
        from_state=OrderState.DRAFT,
        to_state=OrderState.SUBMITTED,
    )
    payload = event.to_dict()
    assert payload["from_state"] == "draft"
    assert payload["to_state"] == "submitted"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
