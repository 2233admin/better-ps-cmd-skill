"""Strategy catalog and status API."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...strategy.engine import get_strategy_engine

router = APIRouter(prefix="/strategy", tags=["strategy"])


class StrategyAction(BaseModel):
    name: str
    params: dict | None = None


@router.get("/list")
async def list_strategies():
    engine = get_strategy_engine()
    return {"data": engine.list_strategies()}


@router.post("/start")
async def start_strategy(action: StrategyAction):
    engine = get_strategy_engine()
    ok = engine.start_strategy(action.name, action.params or {})
    if not ok:
        raise HTTPException(400, f"Failed to start strategy: {action.name}")
    return {"status": "started", "name": action.name}


@router.post("/stop")
async def stop_strategy(action: StrategyAction):
    engine = get_strategy_engine()
    engine.stop_strategy(action.name)
    return {"status": "stopped", "name": action.name}


@router.get("/status/{name}")
async def strategy_status(name: str):
    engine = get_strategy_engine()
    status = engine.get_strategy_status(name)
    if status is None:
        raise HTTPException(404, f"Strategy not found: {name}")
    return status


class TTarget(BaseModel):
    code: str
    mode: str = "long_t"
    volume: int = 100


class TStrategySetup(BaseModel):
    targets: list[TTarget]
    config: dict | None = None


@router.post("/t/setup")
async def setup_t_strategy(req: TStrategySetup):
    from ...strategy.t_strategy import TStrategyConfig, get_t_strategy

    t = get_t_strategy()
    if req.config:
        t.config = TStrategyConfig(**req.config)
    t.set_targets([t_.model_dump() for t_ in req.targets])

    engine = get_strategy_engine()
    engine.start_strategy("t_trading", {"min_confidence": 0.5})

    return {
        "status": "ok",
        "targets": len(req.targets),
        "details": [{"code": t_.code, "mode": t_.mode} for t_ in req.targets],
    }


@router.get("/t/status")
async def t_strategy_status():
    from ...strategy.t_strategy import get_t_strategy

    return get_t_strategy().get_status()


@router.post("/t/reset")
async def t_strategy_reset():
    from ...strategy.t_strategy import get_t_strategy

    get_t_strategy().reset_daily()
    return {"status": "reset"}
