"""Render auditable backtest artifacts."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Iterable

import polars as pl

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


def write_backtest_tables(results: dict[str, LedgerBacktestResult], out_dir: Path) -> None:
    """Write run-level parquet tables for scan-scale backtest consumers."""

    out_dir.mkdir(parents=True, exist_ok=True)
    _frame(_summary_rows(results)).write_parquet(out_dir / "backtest_summary.parquet")
    orders = _frame(_child_rows(results, "orders"))
    fills = _frame(_child_rows(results, "fills"))
    positions = _frame(_child_rows(results, "daily_ledger"))
    orders.write_parquet(out_dir / "backtest_orders.parquet")
    fills.write_parquet(out_dir / "portfolio_fills.parquet")
    _frame(_child_rows(results, "trades")).write_parquet(out_dir / "backtest_trades.parquet")
    positions.write_parquet(out_dir / "backtest_equity_curve.parquet")
    orders.write_parquet(out_dir / "portfolio_orders.parquet")
    positions.write_parquet(out_dir / "portfolio_positions.parquet")
    _portfolio_equity_curve(results).write_parquet(out_dir / "portfolio_equity_curve.parquet")


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


def _frame(rows: list[dict]) -> pl.DataFrame:
    if rows:
        return pl.DataFrame(rows)
    return pl.DataFrame([{}]).head(0)


def _summary_rows(results: dict[str, LedgerBacktestResult]) -> list[dict]:
    rows: list[dict] = []
    for symbol, result in sorted(results.items()):
        rows.append(
            {
                "symbol": symbol,
                "window": result.window,
                "initial_capital": result.initial_capital,
                "final_equity": result.final_equity,
                "total_return": result.total_return,
                "max_drawdown": result.max_drawdown,
                "orders": len(result.orders),
                "fills": len(result.fills),
                "trades": len(result.trades),
                "rejected_orders": sum(1 for order in result.orders if order.status == "rejected"),
            }
            | result.metrics()
        )
    return rows


def _child_rows(results: dict[str, LedgerBacktestResult], attr: str) -> list[dict]:
    rows: list[dict] = []
    for symbol, result in sorted(results.items()):
        for item in getattr(result, attr):
            rows.append({"symbol": symbol} | _normalize(asdict(item)))
    return rows


def _portfolio_equity_curve(results: dict[str, LedgerBacktestResult]) -> pl.DataFrame:
    rows = _child_rows(results, "daily_ledger")
    if not rows:
        return pl.DataFrame([{}]).head(0)
    frame = pl.DataFrame(rows)
    return (
        frame.group_by("date")
        .agg(
            pl.col("cash").sum().alias("cash"),
            pl.col("position_value").sum().alias("position_value"),
            pl.col("total_equity").sum().alias("total_equity"),
            pl.col("daily_pnl").sum().alias("daily_pnl"),
            pl.col("drawdown").max().alias("max_symbol_drawdown"),
        )
        .sort("date")
    )
