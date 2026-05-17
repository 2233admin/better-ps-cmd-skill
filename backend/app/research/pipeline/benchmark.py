"""Benchmark the A-share research pipeline and emit gate metrics."""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from .data_lake import resolve_kline_daily_symbols
from .runner import PipelineConfig, run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Benchmark date in YYYY-MM-DD format")
    parser.add_argument("--data-root", required=True, help="A-share lake root")
    parser.add_argument("--out", required=True, help="Output run directory")
    parser.add_argument("--code-commit", default="manual", help="Git commit or explicit code id")
    parser.add_argument("--symbols", help="Optional comma-separated symbol subset")
    parser.add_argument("--world-snapshot", help="Optional world_snapshot.parquet universe")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_root = Path(args.data_root)
    symbols = _symbols(args, data_root)
    started = time.perf_counter()
    result = run_pipeline(
        PipelineConfig(
            package_date=date.fromisoformat(args.date),
            symbols=symbols,
            out_dir=out_dir / "pipeline",
            data_root=data_root,
            world_snapshot=Path(args.world_snapshot) if args.world_snapshot else None,
            code_commit=args.code_commit,
        )
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    benchmark = _benchmark_payload(out_dir / "pipeline", result.control_report, duration_ms)
    _write_json(out_dir / "benchmark.json", benchmark)
    _write_csv(out_dir / "benchmark.csv", benchmark)
    _write_json(out_dir / "control_report.compact.json", _compact_control(result.control_report, duration_ms))
    return 0


def _symbols(args: argparse.Namespace, data_root: Path) -> tuple[str, ...]:
    if args.symbols:
        return tuple(symbol.strip() for symbol in args.symbols.split(",") if symbol.strip())
    if args.world_snapshot:
        frame = pl.read_parquet(args.world_snapshot)
        if "research_eligible" in frame.columns:
            frame = frame.filter(pl.col("research_eligible"))
        return tuple(sorted(str(symbol) for symbol in frame["symbol"].unique().to_list()))
    return resolve_kline_daily_symbols(data_root)


def _benchmark_payload(out_dir: Path, control_report: Any, duration_ms: int) -> dict[str, Any]:
    observed = dict(control_report.observed_state)
    input_rows = int(observed.get("input_rows", 0))
    requested_symbols = int(observed.get("requested_symbol_count", 0))
    visible_symbols = int(observed.get("visible_symbol_count", 0))
    duration_seconds = max(duration_ms / 1000.0, 1e-9)
    return {
        "duration_ms": duration_ms,
        "input_rows": input_rows,
        "requested_symbols": requested_symbols,
        "visible_symbols": visible_symbols,
        "factor_values": int(observed.get("factor_values", 0)),
        "signals": int(observed.get("signals", 0)),
        "backtests": int(observed.get("backtests", 0)),
        "trades": int(observed.get("completed_trades", 0)),
        "rejected_orders": int(observed.get("rejected_orders", 0)),
        "rows_per_sec": input_rows / duration_seconds,
        "symbols_per_sec": requested_symbols / duration_seconds,
        "decision": control_report.decision,
        "pipeline_dir": str(out_dir),
    }


def _compact_control(control_report: Any, duration_ms: int) -> dict[str, Any]:
    return {
        "decision": control_report.decision,
        "evidence_level": control_report.evidence_level,
        "halt_reasons": list(control_report.halt_reasons),
        "warnings": list(control_report.warnings),
        "manifest_hash": control_report.manifest_hash,
        "dataset_version": control_report.dataset_version,
        "duration_ms": duration_ms,
        "observed_state": _compact_observed_state(control_report.observed_state),
        "invariants": control_report.invariants,
    }


def _compact_observed_state(observed: dict[str, Any]) -> dict[str, Any]:
    compact = dict(observed)
    for key in ("requested_symbols", "visible_symbols"):
        values = compact.pop(key, ())
        compact[f"{key}_sample"] = list(values[:20]) if isinstance(values, list | tuple) else []
    missing = compact.get("empty_or_missing_symbols", ())
    if isinstance(missing, list | tuple) and len(missing) > 20:
        compact["empty_or_missing_symbols"] = list(missing[:20])
        compact["empty_or_missing_symbols_truncated"] = len(missing) - 20
    return compact


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, default=str, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")


def _write_csv(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(payload))
        writer.writeheader()
        writer.writerow(payload)


if __name__ == "__main__":
    raise SystemExit(main())
