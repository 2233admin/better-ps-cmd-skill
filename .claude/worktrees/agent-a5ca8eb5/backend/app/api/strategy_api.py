"""策略管理 API"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..strategy.engine import get_strategy_engine
from ..strategy.backtest import VectorBacktester

router = APIRouter(prefix="/strategy", tags=["strategy"])


class StrategyAction(BaseModel):
    name: str
    params: dict | None = None


@router.get("/list")
async def list_strategies():
    """获取策略列表"""
    engine = get_strategy_engine()
    strategies = engine.list_strategies()
    return {"data": strategies}


@router.post("/start")
async def start_strategy(action: StrategyAction):
    """启动策略"""
    engine = get_strategy_engine()
    ok = engine.start_strategy(action.name, action.params or {})
    if not ok:
        raise HTTPException(400, f"Failed to start strategy: {action.name}")
    return {"status": "started", "name": action.name}


@router.post("/stop")
async def stop_strategy(action: StrategyAction):
    """停止策略"""
    engine = get_strategy_engine()
    engine.stop_strategy(action.name)
    return {"status": "stopped", "name": action.name}


@router.get("/status/{name}")
async def strategy_status(name: str):
    """获取策略状态"""
    engine = get_strategy_engine()
    status = engine.get_strategy_status(name)
    if status is None:
        raise HTTPException(404, f"Strategy not found: {name}")
    return status


# ---- 做T策略 API ----

class TTarget(BaseModel):
    code: str
    mode: str = "long_t"  # long_t / short_t / scalp
    volume: int = 100


class TStrategySetup(BaseModel):
    targets: list[TTarget]
    config: dict | None = None


@router.post("/t/setup")
async def setup_t_strategy(req: TStrategySetup):
    """配置做T策略目标"""
    from ..strategy.t_strategy import get_t_strategy, TStrategyConfig

    t = get_t_strategy()
    if req.config:
        t.config = TStrategyConfig(**req.config)
    t.set_targets([t_.model_dump() for t_ in req.targets])

    # 自动启动 t_trading 策略
    engine = get_strategy_engine()
    engine.start_strategy("t_trading", {"min_confidence": 0.5})

    return {
        "status": "ok",
        "targets": len(req.targets),
        "details": [{"code": t_.code, "mode": t_.mode} for t_ in req.targets],
    }


@router.get("/t/status")
async def t_strategy_status():
    """获取做T策略状态"""
    from ..strategy.t_strategy import get_t_strategy

    t = get_t_strategy()
    return t.get_status()


@router.post("/t/reset")
async def t_strategy_reset():
    """重置做T策略日内状态"""
    from ..strategy.t_strategy import get_t_strategy

    t = get_t_strategy()
    t.reset_daily()
    return {"status": "reset"}


# ---- OKX 加密货币做T API ----

class OKXTSetup(BaseModel):
    targets: list[TTarget]
    mode: str = "paper"  # "paper" or "okx"
    interval: float = 2.0


@router.post("/okx-t/start")
async def start_okx_t(req: OKXTSetup):
    """启动 OKX 加密货币做T"""
    import asyncio
    from ..strategy.okx_t_runner import start_okx_t_runner

    runner = start_okx_t_runner(
        targets=[t_.model_dump() for t_ in req.targets],
        mode=req.mode,
        interval=req.interval,
    )
    # 后台运行
    asyncio.create_task(runner.run())
    return {
        "status": "started",
        "mode": req.mode,
        "pairs": runner.pairs,
        "interval": req.interval,
    }


@router.post("/okx-t/stop")
async def stop_okx_t():
    """停止 OKX 做T"""
    from ..strategy.okx_t_runner import get_okx_t_runner

    runner = get_okx_t_runner()
    if runner:
        runner.stop()
        return {"status": "stopped", "signals": len(runner._signals_log)}
    return {"status": "not_running"}


@router.get("/okx-t/status")
async def okx_t_status():
    """获取 OKX 做T状态"""
    from ..strategy.okx_t_runner import get_okx_t_runner

    runner = get_okx_t_runner()
    if runner:
        return runner.get_status()
    return {"running": False}


class BacktestRequest(BaseModel):
    code: str
    strategy: str
    start_date: str | None = None
    end_date: str | None = None
    initial_capital: float = 1_000_000
    params: dict | None = None


@router.post("/backtest")
async def run_backtest(req: BacktestRequest):
    """运行策略回测"""
    import polars as pl
    from ..data.store import get_store
    from ..strategy.signals import generate_signals

    store = get_store()

    # 获取历史数据 (优先日线)
    try:
        data = store.get_kline_daily(req.code, start_date=req.start_date, end_date=req.end_date)
    except Exception as e:
        raise HTTPException(400, f"Failed to load data for {req.code}: {e}")

    if data is None or data.is_empty():
        raise HTTPException(404, f"No data found for {req.code}")

    # 生成信号列
    time_col = "datetime" if "datetime" in data.columns else "date"
    params = req.params or {}

    # 逐行生成信号 (简化: 用 quote-like dict 模拟)
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
        if sigs:
            sig = sigs[0]
            signal_values.append(1 if sig.direction == "buy" else -1)
        else:
            signal_values.append(0)

    signals_df = pl.DataFrame({
        time_col: data[time_col],
        "signal": signal_values,
    })

    # 运行回测
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
        "equity_curve": result.equity_curve[-100:],  # 最后100个点
        "trade_count": len(result.trade_log),
    }
