"""Unit tests for trading.recon_state."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.trading.recon_state import (
    QUARANTINE_AFTER_ATTEMPTS,
    IntentState,
    append,
    load_latest,
    step,
)


def _state(intent_id: str = "req-1") -> IntentState:
    return IntentState(intent_id=intent_id)


def test_pending_to_submitted_on_acceptance():
    s = step(_state(), in_accepted=True, in_rejected=False, in_fills=False, reconciled=False, now="t1")
    assert s.status == "submitted"
    assert s.history[-1]["to"] == "submitted"


def test_submitted_to_filled():
    s = IntentState(intent_id="req-1", status="submitted")
    s = step(s, in_accepted=True, in_rejected=False, in_fills=True, reconciled=False, now="t2")
    assert s.status == "filled"


def test_filled_to_reconciled():
    s = IntentState(intent_id="req-1", status="filled")
    s = step(s, in_accepted=False, in_rejected=False, in_fills=True, reconciled=True, now="t3")
    assert s.status == "reconciled"


def test_terminal_reconciled_does_not_transition():
    s = IntentState(intent_id="req-1", status="reconciled")
    s2 = step(s, in_accepted=True, in_rejected=True, in_fills=True, reconciled=True, now="t4")
    assert s2.status == "reconciled"
    assert s2 is s  # no new history record


def test_rejection_increments_attempts_and_quarantines():
    s = _state()
    for i in range(QUARANTINE_AFTER_ATTEMPTS):
        s = step(s, in_accepted=False, in_rejected=True, in_fills=False, reconciled=False, now=f"t{i}", reason="risk_reject")
    assert s.status == "quarantined"
    assert s.attempts == QUARANTINE_AFTER_ATTEMPTS


def test_quarantined_is_terminal():
    s = IntentState(intent_id="req-1", status="quarantined", attempts=QUARANTINE_AFTER_ATTEMPTS)
    s2 = step(s, in_accepted=True, in_rejected=False, in_fills=True, reconciled=True, now="later")
    assert s2.status == "quarantined"


def test_idempotent_no_observation_keeps_state():
    s = IntentState(intent_id="req-1", status="submitted")
    s2 = step(s, in_accepted=False, in_rejected=False, in_fills=False, reconciled=False, now="t")
    assert s2.status == "submitted"
    assert s2.history == s.history


def test_jsonl_append_and_load_replays_latest(tmp_path: Path):
    log = tmp_path / "recon.jsonl"
    a = step(_state("A"), in_accepted=True, in_rejected=False, in_fills=False, reconciled=False, now="t1")
    append(log, a)
    a = step(a, in_accepted=True, in_rejected=False, in_fills=True, reconciled=False, now="t2")
    append(log, a)
    b = step(_state("B"), in_accepted=True, in_rejected=False, in_fills=False, reconciled=False, now="t1")
    append(log, b)

    latest = load_latest(log)
    assert set(latest) == {"A", "B"}
    assert latest["A"].status == "filled"
    assert latest["B"].status == "submitted"


def test_load_latest_missing_file_returns_empty(tmp_path: Path):
    assert load_latest(tmp_path / "nope.jsonl") == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
