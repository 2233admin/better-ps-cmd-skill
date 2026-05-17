"""Auditable research backtest contracts."""

from .engine import (
    AShareBacktestConfig,
    AShareLedgerBacktester,
    CryptoBacktestConfig,
    CryptoLedgerBacktester,
)
from .models import (
    DailyLedgerRecord,
    FillRecord,
    LedgerBacktestResult,
    OrderRecord,
    TradeRecord,
)
from .renderers import write_backtest_artifacts, write_backtest_tables

__all__ = [
    "AShareBacktestConfig",
    "AShareLedgerBacktester",
    "CryptoBacktestConfig",
    "CryptoLedgerBacktester",
    "DailyLedgerRecord",
    "FillRecord",
    "LedgerBacktestResult",
    "OrderRecord",
    "TradeRecord",
    "write_backtest_artifacts",
    "write_backtest_tables",
]
