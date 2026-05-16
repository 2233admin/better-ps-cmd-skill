"""Backtest semantics that must stay stable for research signals."""

from datetime import datetime, timedelta

import polars as pl

from quant_terminal.core.backtest import BacktestConfig, BacktestEngine


def _ohlcv(closes):
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))]
    return pl.DataFrame(
        {
            "datetime": dates,
            "open": closes,
            "high": [price + 1 for price in closes],
            "low": [price - 1 for price in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
        }
    )


def _signals(values):
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(len(values))]
    return pl.DataFrame({"datetime": dates, "signal": values})


def test_signal_pnl_uses_next_bar_position():
    engine = BacktestEngine(BacktestConfig(commission=0.0, slippage=0.0))

    result = engine.run(_ohlcv([100.0, 110.0, 121.0]), _signals([1, 0, 0]))
    daily_returns = result.equity_curve["daily_return"].to_list()

    assert daily_returns[0] == 0.0
    assert daily_returns[1] == 0.1
    assert daily_returns[2] == 0.0


def test_costs_are_charged_only_on_position_changes():
    engine = BacktestEngine(BacktestConfig(commission=0.01, slippage=0.0))

    result = engine.run(_ohlcv([100.0, 100.0, 100.0, 100.0]), _signals([1, 1, 0, 0]))
    daily_returns = result.equity_curve["daily_return"].to_list()

    assert daily_returns == [-0.01, 0.0, -0.01, 0.0]


def test_trade_ledger_extracts_completed_trade():
    engine = BacktestEngine(BacktestConfig(commission=0.0, slippage=0.0))

    result = engine.run(_ohlcv([100.0, 110.0, 121.0, 121.0]), _signals([1, 1, 0, 0]))

    assert result.total_trades == 1
    assert result.trades.row(0, named=True)["direction"] == "long"
    assert result.trades.row(0, named=True)["bars_held"] == 2
    assert round(result.trades.row(0, named=True)["pnl"], 2) == 0.21
