"""Research backtest API."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...strategy.backtest import VectorBacktester

router = APIRouter(prefix="/strategy", tags=["backtest"])


class BacktestRequest(BaseModel):
    code: str
    strategy: str
    start_date: str | None = None
    end_date: str | None = None
    initial_capital: float = 1_000_000
    params: dict | None = None


@router.post("/backtest")
async def run_backtest(req: BacktestRequest):
    import polars as pl

    from ...data.store import get_store
    from ...strategy.signals import generate_signals

    store = get_store()
    try:
        data = store.get_kline_daily(req.code, start_date=req.start_date, end_date=req.end_date)
    except Exception as e:
        raise HTTPException(400, f"Failed to load data for {req.code}: {e}")

    if data is None or data.is_empty():
        raise HTTPException(404, f"No data found for {req.code}")

    time_col = "datetime" if "datetime" in data.columns else "date"
    params = req.params or {}
    signal_values = []
    for row in data.iter_rows(named=True):
        quote = {
            "code": req.code,
            "price": row.get("close", 0),
            "vol": row.get("volume", 0),
            "open": row.get("open", 0),
            "high": row.get("high", 0),
            "low": row.get("low", 0),
        }
        sigs = generate_signals(req.strategy, [quote], params)
        signal_values.append(1 if sigs and sigs[0].direction == "buy" else -1 if sigs else 0)

    signals_df = pl.DataFrame({time_col: data[time_col], "signal": signal_values})
    backtester = VectorBacktester(
        initial_capital=req.initial_capital,
        commission=0.0005,
        slippage=0.001,
    )
    result = backtester.run(data, signals_df)

    return {
        "code": req.code,
        "strategy": req.strategy,
        "result": result.to_dict(),
        "equity_curve": result.equity_curve[-100:],
        "trade_count": len(result.trade_log),
    }
