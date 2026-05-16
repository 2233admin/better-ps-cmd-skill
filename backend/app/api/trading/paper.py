"""Paper trading API."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...trading.executor import get_executor
from ...trading.intent import TradingMode, require_trading_mode

router = APIRouter(prefix="/trading/paper", tags=["paper-trading"])


class PaperOrderRequest(BaseModel):
    code: str
    direction: str
    price: float
    volume: int
    strategy: str = "manual"


@router.post("/orders")
async def submit_paper_order(order: PaperOrderRequest):
    try:
        require_trading_mode(TradingMode.PAPER)
    except RuntimeError as exc:
        raise HTTPException(403, str(exc))
    executor = get_executor()
    result = executor.submit_order(
        code=order.code,
        direction=order.direction,
        price=order.price,
        volume=order.volume,
        strategy=order.strategy,
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result
