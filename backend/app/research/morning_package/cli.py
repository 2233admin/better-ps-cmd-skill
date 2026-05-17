"""CLI for generating the A-share morning decision package."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.research.manifest import sha256_json

from .builder import build_morning_package
from .models import (
    BacktestEvidence,
    CandidateSignal,
    EvidenceChain,
    EvidenceSource,
    RiskBlock,
)
from .renderers import write_artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Package date in YYYY-MM-DD format")
    parser.add_argument("--out", required=True, help="Output artifact directory")
    parser.add_argument("--input", help="Optional manual JSON payload")
    parser.add_argument(
        "--scenario",
        choices=("trade", "observe", "do_not_trade"),
        default="trade",
        help="Built-in fixture scenario used when --input is omitted",
    )
    args = parser.parse_args(argv)

    package_date = date.fromisoformat(args.date)
    payload = _load_payload(Path(args.input)) if args.input else _fixture_payload(args.scenario)
    package = _package_from_payload(package_date, payload)
    write_artifacts(package, Path(args.out))
    return 0


def _load_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _package_from_payload(package_date: date, payload: dict[str, Any]):
    return build_morning_package(
        package_date=package_date,
        candidates=tuple(_candidate(item) for item in payload.get("candidates", [])),
        manifest_hash=payload.get("manifest_hash"),
        dataset_version=payload.get("dataset_version"),
        code_commit=payload.get("code_commit"),
        config_hash=payload.get("config_hash"),
        risk=RiskBlock(
            market_risks=tuple(payload.get("market_risks", [])),
            anomalies=tuple(payload.get("anomalies", [])),
            data_integrity=tuple(payload.get("data_integrity", [])),
            portfolio_limits=tuple(payload.get("portfolio_limits", [])),
        ),
        audit_context=payload.get("audit_context", {}),
    )


def _candidate(payload: dict[str, Any]) -> CandidateSignal:
    evidence_payload = payload.get("evidence_chain")
    backtest_payload = payload.get("backtest")
    return CandidateSignal(
        symbol=payload.get("symbol", ""),
        side=payload.get("side", ""),
        thesis=payload.get("thesis", ""),
        evidence_chain=_evidence_chain(evidence_payload) if evidence_payload else None,
        backtest=_backtest(backtest_payload) if backtest_payload else None,
        position_cap=payload.get("position_cap"),
        failure_condition=payload.get("failure_condition", ""),
        auxiliary_notes=payload.get("auxiliary_notes", ""),
    )


def _evidence_chain(payload: dict[str, Any]) -> EvidenceChain:
    return EvidenceChain(
        sources=tuple(
            EvidenceSource(
                kind=item["kind"],
                source_id=item["source_id"],
                description=item["description"],
                uri=item.get("uri", ""),
            )
            for item in payload.get("sources", [])
        ),
        summary=payload.get("summary", ""),
    )


def _backtest(payload: dict[str, Any]) -> BacktestEvidence:
    return BacktestEvidence(
        window=payload.get("window", ""),
        parameters=dict(payload.get("parameters", {})),
        metrics={key: float(value) for key, value in payload.get("metrics", {}).items()},
        max_drawdown=payload.get("max_drawdown"),
    )


def _fixture_payload(scenario: str) -> dict[str, Any]:
    base = {
        "dataset_version": "2026-05-16",
        "code_commit": "fixture-commit",
        "manifest_hash": sha256_json({"fixture": scenario}),
        "config_hash": sha256_json({"max_opportunities": 3}),
        "market_risks": ["event risk checked before open"],
        "data_integrity": ["PIT daily bars available as of prior close"],
        "portfolio_limits": ["single-name cap <= 5%"],
        "candidates": [_complete_candidate()],
        "audit_context": {"risk_metrics": {"portfolio_var": 0.0}},
    }
    if scenario == "observe":
        base["candidates"][0]["failure_condition"] = ""
    if scenario == "do_not_trade":
        base["manifest_hash"] = None
    return base


def _complete_candidate() -> dict[str, Any]:
    return {
        "symbol": "600000.SH",
        "side": "buy",
        "thesis": "20-day momentum with controlled drawdown and PIT data lineage.",
        "evidence_chain": {
            "summary": "PIT data plus backtest evidence.",
            "sources": [
                {
                    "kind": "dataset",
                    "source_id": "ashare.kline_daily_pit:2026-05-16",
                    "description": "PIT daily kline snapshot.",
                },
                {
                    "kind": "backtest",
                    "source_id": "bt-momentum-20d",
                    "description": "Walk-forward backtest artifact.",
                },
            ],
        },
        "backtest": {
            "window": "2024-01-01/2026-05-16",
            "parameters": {"lookback": 20, "rebalance": "daily"},
            "metrics": {"sharpe": 1.2, "win_rate": 0.54},
            "max_drawdown": 0.08,
        },
        "position_cap": 0.05,
        "failure_condition": "Breaks below 20-day moving average with volume expansion.",
    }


if __name__ == "__main__":
    raise SystemExit(main())
