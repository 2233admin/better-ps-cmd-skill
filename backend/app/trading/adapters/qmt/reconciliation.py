"""Reconcile k-atana intents against an EasyXT/QMT sim journal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def reconcile_easyxt_sim(
    intents: list[dict[str, Any]],
    *,
    accepted: list[dict[str, Any]] | None = None,
    rejected: list[dict[str, Any]] | None = None,
    fills: list[dict[str, Any]] | None = None,
    expected_positions: dict[str, float] | None = None,
    actual_positions: dict[str, float] | None = None,
    expected_cash: float | None = None,
    actual_cash: float | None = None,
    cash_tolerance: float = 0.01,
) -> dict[str, Any]:
    """Return a compact pass/fail report for the L3 sim boundary."""

    accepted = accepted or []
    rejected = rejected or []
    fills = fills or []
    expected_positions = expected_positions or {}
    actual_positions = actual_positions or {}
    position_diff = _position_diff(expected_positions, actual_positions)
    cash_diff = (
        round(float(actual_cash) - float(expected_cash), 6)
        if expected_cash is not None and actual_cash is not None
        else 0.0
    )
    intent_ids = {_request_id(item) for item in intents}
    journal_ids = {_request_id(item) for item in accepted + rejected}
    missing_journal_ids = tuple(sorted(intent_ids - journal_ids))
    unexpected_journal_ids = tuple(sorted(journal_ids - intent_ids))
    decision = "pass"
    reasons: list[str] = []
    if missing_journal_ids:
        reasons.append("intent missing from EasyXT journal")
    if unexpected_journal_ids:
        reasons.append("EasyXT journal contains unknown request_id")
    if position_diff:
        reasons.append("position diff")
    if abs(cash_diff) > cash_tolerance:
        reasons.append("cash diff")
    if len(accepted) + len(rejected) != len(intents):
        reasons.append("accepted plus rejected count differs from intent count")
    if reasons:
        decision = "fail"
    return {
        "intent_count": len(intents),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "fills": len(fills),
        "position_diff": position_diff,
        "cash_diff": cash_diff,
        "missing_journal_ids": missing_journal_ids,
        "unexpected_journal_ids": unexpected_journal_ids,
        "decision": decision,
        "reasons": tuple(reasons),
    }


def reconcile_easyxt_sim_files(
    *,
    intents_path: Path,
    journal_path: Path,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Load JSON artifacts and optionally write a reconciliation report."""

    intents = _load_json_list(intents_path)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    report = reconcile_easyxt_sim(
        intents,
        accepted=list(journal.get("accepted", [])),
        rejected=list(journal.get("rejected", [])),
        fills=list(journal.get("fills", [])),
        expected_positions=dict(journal.get("expected_positions", {})),
        actual_positions=dict(journal.get("actual_positions", {})),
        expected_cash=journal.get("expected_cash"),
        actual_cash=journal.get("actual_cash"),
    )
    if out_path is not None:
        out_path.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return report


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if isinstance(payload, dict) and isinstance(payload.get("intents"), list):
        return [dict(item) for item in payload["intents"]]
    raise ValueError(f"expected JSON list or intents object: {path}")


def _request_id(item: dict[str, Any]) -> str:
    return str(item.get("request_id") or item.get("intent_id") or item.get("order_id") or "")


def _position_diff(expected: dict[str, float], actual: dict[str, float]) -> dict[str, float]:
    symbols = set(expected) | set(actual)
    diff = {
        symbol: round(float(actual.get(symbol, 0.0)) - float(expected.get(symbol, 0.0)), 6)
        for symbol in symbols
    }
    return {symbol: value for symbol, value in sorted(diff.items()) if value}
