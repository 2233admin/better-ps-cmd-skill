"""Validate the minimum A-share PIT lake contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl


REQUIRED_COLUMNS = {
    "symbol",
    "market",
    "event_time",
    "available_at",
    "source_updated_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="A-share lake root")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    args = parser.parse_args(argv)

    root = Path(args.root)
    path = root / "pit" / "kline_daily_pit.parquet"
    report = validate_kline_daily(path)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if report["passed"] else 1


def validate_kline_daily(path: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {"passed": False, "path": str(path), "errors": [f"missing parquet: {path}"], "warnings": []}

    frame = pl.read_parquet(path)
    columns = set(frame.columns)
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        errors.append(f"missing columns: {', '.join(missing)}")
        return _report(path, frame, errors, warnings)

    duplicate_rows = frame.select(pl.struct(["symbol", "event_time"]).is_duplicated().sum()).item()
    if duplicate_rows:
        errors.append(f"duplicate symbol/event_time rows: {duplicate_rows}")
    if frame.filter(pl.col("source_updated_at") < pl.col("available_at")).height:
        errors.append("source_updated_at before available_at")
    if frame.filter(pl.col("available_at") < pl.col("event_time")).height:
        errors.append("available_at before event_time")
    if frame.filter((pl.col("high") < pl.col("low")) | (pl.col("close") <= 0) | (pl.col("open") <= 0)).height:
        errors.append("invalid OHLC values")
    if frame.filter(pl.col("volume") < 0).height:
        errors.append("negative volume")
    if frame.filter(pl.col("amount") < 0).height:
        errors.append("negative amount")

    for symbol in frame["symbol"].unique().to_list():
        times = frame.filter(pl.col("symbol") == symbol)["event_time"].to_list()
        if any(times[idx] < times[idx - 1] for idx in range(1, len(times))):
            errors.append(f"non-monotonic event_time: {symbol}")

    return _report(path, frame, errors, warnings)


def _report(path: Path, frame: pl.DataFrame, errors: list[str], warnings: list[str]) -> dict:
    symbols = sorted(frame["symbol"].unique().to_list()) if "symbol" in frame.columns and frame.height else []
    return {
        "passed": not errors,
        "path": str(path),
        "rows": frame.height,
        "symbols": symbols,
        "start": str(frame["event_time"].min()) if "event_time" in frame.columns and frame.height else None,
        "end": str(frame["event_time"].max()) if "event_time" in frame.columns and frame.height else None,
        "errors": errors,
        "warnings": warnings,
    }


if __name__ == "__main__":
    raise SystemExit(main())
