"""订单 API"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..trading.executor import get_executor
from ..trading.intent import TradingMode, require_trading_mode

router = APIRouter(prefix="/order", tags=["order"])


class OrderRequest(BaseModel):
    code: str
    direction: str  # "buy" or "sell"
    price: float
    volume: int
    strategy: str = "manual"


@router.post("/submit")
async def submit_order(order: OrderRequest):
    """提交订单"""
    try:
        require_trading_mode(TradingMode.PAPER, TradingMode.LIVE_TEST)
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


@router.post("/cancel/{order_id}")
async def cancel_order(order_id: int):
    """撤销订单"""
    executor = get_executor()
    result = executor.cancel_order(order_id)
    if not result:
        raise HTTPException(400, f"Failed to cancel order {order_id}")
    return {"status": "cancelled", "order_id": order_id}


@router.get("/pending")
async def get_pending_orders():
    """获取未成交订单"""
    executor = get_executor()
    orders = executor.get_pending_orders()
    return {"data": orders, "count": len(orders)}
