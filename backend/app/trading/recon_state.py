"""JSONL-backed reconciliation state for trading intents.

Plain dict-of-states keyed by intent_id, persisted as JSONL append log.
Transitions are a flat function — no state-machine framework.

Statuses:
    pending      - intent emitted, not yet seen in journal
    submitted    - journal recorded acceptance, no fill yet
    filled       - journal recorded full or partial fill
    reconciled   - position + cash match expectation
    quarantined  - failed N attempts; needs human

Caller responsibilities:
    - assign stable intent_id per OrderIntent
    - call step() once per reconcile cycle with current journal snapshot
    - persist via append() after each transition
    - read latest snapshot via load_latest()
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Status = Literal["pending", "submitted", "filled", "reconciled", "quarantined"]

QUARANTINE_AFTER_ATTEMPTS = 5
TERMINAL: tuple[Status, ...] = ("reconciled", "quarantined")


@dataclass
class IntentState:
    intent_id: str
    status: Status = "pending"
    attempts: int = 0
    last_event_at: str = ""
    last_reason: str = ""
    history: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntentState":
        return cls(
            intent_id=str(payload["intent_id"]),
            status=payload.get("status", "pending"),
            attempts=int(payload.get("attempts", 0)),
            last_event_at=str(payload.get("last_event_at", "")),
            last_reason=str(payload.get("last_reason", "")),
            history=list(payload.get("history", [])),
        )


def step(
    state: IntentState,
    *,
    in_accepted: bool,
    in_rejected: bool,
    in_fills: bool,
    reconciled: bool,
    now: str,
    reason: str = "",
) -> IntentState:
    """Compute next state from current state + journal observations.

    Idempotent: calling with identical observations returns the same status.
    Terminal states (reconciled, quarantined) never transition.
    """

    if state.status in TERMINAL:
        return state

    next_status: Status = state.status
    if reconciled:
        next_status = "reconciled"
    elif in_fills:
        next_status = "filled"
    elif in_rejected:
        # rejection counts as an attempt; quarantine once over threshold
        state = _record(state, state.status, now, reason or "rejected")
        state.attempts += 1
        if state.attempts >= QUARANTINE_AFTER_ATTEMPTS:
            next_status = "quarantined"
        else:
            next_status = "pending"
    elif in_accepted:
        next_status = "submitted"

    if next_status != state.status:
        state = _record(state, next_status, now, reason)
        state.status = next_status
        state.last_event_at = now
        state.last_reason = reason
    return state


def append(path: Path, state: IntentState) -> None:
    """Append one state snapshot to the JSONL log."""

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(state.to_dict(), ensure_ascii=True, sort_keys=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_latest(path: Path) -> dict[str, IntentState]:
    """Return latest state per intent_id by replaying the JSONL log."""

    latest: dict[str, IntentState] = {}
    if not path.exists():
        return latest
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        payload = json.loads(raw)
        state = IntentState.from_dict(payload)
        latest[state.intent_id] = state
    return latest


def _record(state: IntentState, target: Status, now: str, reason: str) -> IntentState:
    entry = {"at": now, "from": state.status, "to": target, "reason": reason}
    state.history.append(entry)
    return state
