"""Portfolio attribution layer for A-share research backtests.

Provides:
- equal_weight_index / cap_weight_index: synthetic benchmark daily return series
- per_symbol_contribution: daily PnL by symbol from LedgerBacktestResult set
- attribute_vs_benchmark: alpha, beta, tracking error, information ratio

Brinson sector decomposition is NOT included -- it requires a PIT sector
classification dataset that has no producer yet. Add when the sector lake lands.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Mapping

import polars as pl

from app.research.backtest.models import LedgerBacktestResult


def equal_weight_index(prices: pl.DataFrame) -> pl.DataFrame:
    """Equal-weight daily return index.

    Input: long frame with columns (date, symbol, close). All symbols rebalanced
    daily to equal weight before computing the cross-sectional mean return.
    Output: (date, index_return) sorted ascending by date.
    """

    _require_columns(prices, ("date", "symbol", "close"))
    returns = (
        prices.sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("ret")
        )
        .filter(pl.col("ret").is_not_null())
    )
    index = (
        returns.group_by("date")
        .agg(pl.col("ret").mean().alias("index_return"))
        .sort("date")
    )
    return index


def cap_weight_index(prices: pl.DataFrame, market_caps: pl.DataFrame) -> pl.DataFrame:
    """Market-cap-weighted daily return index.

    prices: long frame (date, symbol, close).
    market_caps: long frame (date, symbol, market_cap) — weights at start of
        each day. Missing rows fall back to 0 weight for that day.
    Output: (date, index_return).
    """

    _require_columns(prices, ("date", "symbol", "close"))
    _require_columns(market_caps, ("date", "symbol", "market_cap"))
    returns = (
        prices.sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("ret")
        )
        .filter(pl.col("ret").is_not_null())
    )
    joined = returns.join(market_caps, on=["date", "symbol"], how="left").with_columns(
        pl.col("market_cap").fill_null(0.0)
    )
    weight_totals = joined.group_by("date").agg(pl.col("market_cap").sum().alias("total"))
    joined = joined.join(weight_totals, on="date", how="left").with_columns(
        pl.when(pl.col("total") > 0)
        .then(pl.col("market_cap") / pl.col("total"))
        .otherwise(0.0)
        .alias("weight")
    )
    index = (
        joined.with_columns((pl.col("ret") * pl.col("weight")).alias("weighted"))
        .group_by("date")
        .agg(pl.col("weighted").sum().alias("index_return"))
        .sort("date")
    )
    return index


def per_symbol_contribution(
    backtests: Mapping[str, LedgerBacktestResult],
) -> pl.DataFrame:
    """Daily PnL contribution per symbol, summed across backtests.

    Output: (date, symbol, daily_pnl, position_value, total_equity) — one row
    per (date, symbol). When multiple symbols share a date the totals are
    per-symbol; downstream consumers aggregate as needed.
    """

    rows: list[dict[str, object]] = []
    for symbol, bt in backtests.items():
        for ledger in bt.daily_ledger:
            rows.append(
                {
                    "date": ledger.date,
                    "symbol": symbol,
                    "daily_pnl": float(ledger.daily_pnl),
                    "position_value": float(ledger.position_value),
                    "total_equity": float(ledger.total_equity),
                }
            )
    if not rows:
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "symbol": pl.Utf8,
                "daily_pnl": pl.Float64,
                "position_value": pl.Float64,
                "total_equity": pl.Float64,
            }
        )
    return pl.DataFrame(rows).sort(["date", "symbol"])


def attribute_vs_benchmark(
    portfolio_returns: pl.DataFrame,
    benchmark_returns: pl.DataFrame,
    *,
    annualization_factor: float = 252.0,
) -> dict[str, float]:
    """Compute alpha/beta/tracking-error/info-ratio vs a benchmark.

    Both inputs: (date, <return_col>) where the return column is the only
    non-date column. Returns are aligned on inner join.

    Alpha is annualized (mean active return × annualization_factor).
    Beta is OLS slope of portfolio on benchmark.
    Tracking error is annualized stdev of active returns.
    Information ratio is alpha / tracking_error.
    """

    if portfolio_returns.height == 0 or benchmark_returns.height == 0:
        return _zero_attribution()

    p_col = _single_return_col(portfolio_returns)
    b_col = _single_return_col(benchmark_returns)
    aligned = (
        portfolio_returns.rename({p_col: "p_ret"})
        .join(benchmark_returns.rename({b_col: "b_ret"}), on="date", how="inner")
        .sort("date")
    )
    if aligned.height < 2:
        return _zero_attribution()

    p = aligned["p_ret"].to_numpy()
    b = aligned["b_ret"].to_numpy()
    active = p - b
    mean_active = float(active.mean())
    var_b = float(((b - b.mean()) ** 2).mean())
    cov = float(((p - p.mean()) * (b - b.mean())).mean())
    beta = cov / var_b if var_b > 0 else 0.0
    alpha_annual = mean_active * annualization_factor
    tracking_error = float(active.std(ddof=0)) * (annualization_factor**0.5)
    info_ratio = alpha_annual / tracking_error if tracking_error > 0 else 0.0
    return {
        "alpha_annualized": alpha_annual,
        "beta": beta,
        "tracking_error_annualized": tracking_error,
        "information_ratio": info_ratio,
        "active_return_mean": mean_active,
        "sample_size": float(aligned.height),
    }


def _require_columns(frame: pl.DataFrame, columns: tuple[str, ...]) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"frame missing required columns: {missing}")


def _single_return_col(frame: pl.DataFrame) -> str:
    non_date = [c for c in frame.columns if c != "date"]
    if len(non_date) != 1:
        raise ValueError(
            f"returns frame must have exactly one non-date column, got {non_date}"
        )
    return non_date[0]


def _zero_attribution() -> dict[str, float]:
    return {
        "alpha_annualized": 0.0,
        "beta": 0.0,
        "tracking_error_annualized": 0.0,
        "information_ratio": 0.0,
        "active_return_mean": 0.0,
        "sample_size": 0.0,
    }
