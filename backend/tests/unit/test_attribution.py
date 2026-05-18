"""Unit tests for research.pipeline.attribution."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.research.backtest.models import DailyLedgerRecord, LedgerBacktestResult
from app.research.pipeline.attribution import (
    attribute_vs_benchmark,
    cap_weight_index,
    equal_weight_index,
    per_symbol_contribution,
)


def _prices(symbols: list[str], closes: dict[str, list[float]], dates: list[date]) -> pl.DataFrame:
    rows = []
    for symbol in symbols:
        for d, c in zip(dates, closes[symbol], strict=True):
            rows.append({"date": d, "symbol": symbol, "close": c})
    return pl.DataFrame(rows)


def test_equal_weight_index_averages_cross_section():
    dates = [date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)]
    prices = _prices(
        ["A", "B"],
        {"A": [100.0, 110.0, 121.0], "B": [50.0, 50.0, 50.0]},
        dates,
    )
    idx = equal_weight_index(prices)
    assert idx.height == 2  # first row null-shifted
    # day 2: A=+10%, B=0%, mean=5%
    assert idx["index_return"].to_list()[0] == pytest.approx(0.05)
    # day 3: A=+10%, B=0%, mean=5%
    assert idx["index_return"].to_list()[1] == pytest.approx(0.05)


def test_cap_weight_index_weighted_by_market_cap():
    dates = [date(2026, 5, 1), date(2026, 5, 2)]
    prices = _prices(
        ["A", "B"],
        {"A": [100.0, 120.0], "B": [50.0, 51.0]},
        dates,
    )
    caps = pl.DataFrame(
        [
            {"date": dates[0], "symbol": "A", "market_cap": 9000.0},
            {"date": dates[0], "symbol": "B", "market_cap": 1000.0},
            {"date": dates[1], "symbol": "A", "market_cap": 9000.0},
            {"date": dates[1], "symbol": "B", "market_cap": 1000.0},
        ]
    )
    idx = cap_weight_index(prices, caps)
    # A=+20% with weight 0.9, B=+2% with weight 0.1 -> 0.182
    assert idx["index_return"].to_list()[0] == pytest.approx(0.182, abs=1e-3)


def test_per_symbol_contribution_aggregates_daily_ledger():
    bt_a = LedgerBacktestResult(
        symbol="600000.SH",
        window="2026-05-01/2026-05-02",
        initial_capital=1_000_000.0,
        final_equity=1_005_000.0,
        total_return=0.005,
        max_drawdown=0.0,
        daily_ledger=(
            DailyLedgerRecord(
                date=date(2026, 5, 1),
                cash=900_000.0,
                position_qty=100,
                position_value=100_000.0,
                total_equity=1_000_000.0,
                daily_pnl=0.0,
                drawdown=0.0,
            ),
            DailyLedgerRecord(
                date=date(2026, 5, 2),
                cash=900_000.0,
                position_qty=100,
                position_value=105_000.0,
                total_equity=1_005_000.0,
                daily_pnl=5_000.0,
                drawdown=0.0,
            ),
        ),
    )
    out = per_symbol_contribution({"600000.SH": bt_a})
    assert out.height == 2
    assert out["daily_pnl"].to_list() == [0.0, 5_000.0]
    assert set(out["symbol"].unique().to_list()) == {"600000.SH"}


def test_per_symbol_contribution_empty():
    out = per_symbol_contribution({})
    assert out.height == 0
    assert out.columns == ["date", "symbol", "daily_pnl", "position_value", "total_equity"]


def test_attribute_vs_benchmark_perfect_tracker():
    dates = [date(2026, 5, d) for d in range(1, 11)]
    rets = [0.01, -0.005, 0.015, 0.0, 0.008, -0.002, 0.012, 0.001, -0.006, 0.009]
    p = pl.DataFrame({"date": dates, "ret": rets})
    b = pl.DataFrame({"date": dates, "ret": rets})
    attr = attribute_vs_benchmark(p, b)
    assert attr["alpha_annualized"] == pytest.approx(0.0)
    assert attr["beta"] == pytest.approx(1.0)
    assert attr["tracking_error_annualized"] == pytest.approx(0.0)
    assert attr["sample_size"] == 10.0


def test_attribute_vs_benchmark_returns_zero_on_empty():
    p = pl.DataFrame({"date": [], "ret": []}, schema={"date": pl.Date, "ret": pl.Float64})
    b = pl.DataFrame({"date": [], "ret": []}, schema={"date": pl.Date, "ret": pl.Float64})
    attr = attribute_vs_benchmark(p, b)
    assert attr["alpha_annualized"] == 0.0
    assert attr["sample_size"] == 0.0


def test_attribute_vs_benchmark_nonzero_alpha_and_beta():
    dates = [date(2026, 5, d) for d in range(1, 11)]
    bench_rets = [0.01, -0.01, 0.02, -0.02, 0.005, -0.005, 0.015, -0.015, 0.0, 0.01]
    # portfolio = 1.5 * bench + 0.001 (alpha)
    port_rets = [1.5 * r + 0.001 for r in bench_rets]
    p = pl.DataFrame({"date": dates, "ret": port_rets})
    b = pl.DataFrame({"date": dates, "ret": bench_rets})
    attr = attribute_vs_benchmark(p, b)
    assert attr["beta"] == pytest.approx(1.5, abs=1e-6)
    assert attr["alpha_annualized"] > 0
    assert attr["information_ratio"] > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
