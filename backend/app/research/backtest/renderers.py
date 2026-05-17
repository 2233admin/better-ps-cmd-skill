"""Render auditable backtest artifacts."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Iterable

from .models import LedgerBacktestResult


def write_backtest_artifacts(result: LedgerBacktestResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "orders.csv", [asdict(item) for item in result.orders])
    _write_csv(out_dir / "fills.csv", [asdict(item) for item in result.fills])
    _write_csv(out_dir / "daily_ledger.csv", [asdict(item) for item in result.daily_ledger])
    _write_csv(out_dir / "trades.csv", [asdict(item) for item in result.trades])
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {
                "symbol": result.symbol,
                "window": result.window,
                "parameters": result.parameters,
                "metrics": result.metrics(),
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Iterable[dict]) -> None:
    normalized = [_normalize(row) for row in rows]
    fieldnames = list(normalized[0].keys()) if normalized else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not fieldnames:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(normalized)


def _normalize(row: dict) -> dict:
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in row.items()
    }
