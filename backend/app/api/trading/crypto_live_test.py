"""OKX T-runner API for paper and gated crypto live_test modes."""

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/strategy", tags=["crypto-live-test"])


class TTarget(BaseModel):
    code: str
    mode: str = "long_t"
    volume: int = 100


class OKXTSetup(BaseModel):
    targets: list[TTarget]
    mode: str = "paper"
    interval: float = 2.0


@router.post("/okx-t/start")
async def start_okx_t(req: OKXTSetup):
    from ...strategy.okx_t_runner import start_okx_t_runner

    runner = start_okx_t_runner(
        targets=[t_.model_dump() for t_ in req.targets],
        mode=req.mode,
        interval=req.interval,
    )
    asyncio.create_task(runner.run())
    return {
        "status": "started",
        "mode": req.mode,
        "pairs": runner.pairs,
        "interval": req.interval,
    }


@router.post("/okx-t/stop")
async def stop_okx_t():
    from ...strategy.okx_t_runner import get_okx_t_runner

    runner = get_okx_t_runner()
    if runner:
        runner.stop()
        return {"status": "stopped", "signals": len(runner._signals_log)}
    return {"status": "not_running"}


@router.get("/okx-t/status")
async def okx_t_status():
    from ...strategy.okx_t_runner import get_okx_t_runner

    runner = get_okx_t_runner()
    if runner:
        return runner.get_status()
    return {"running": False}
