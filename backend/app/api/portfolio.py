"""持仓和交易记录 API"""

from fastapi import APIRouter, Query

from ..data.store import get_store
from ..trading.executor import get_executor

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/positions")
async def get_positions():
    """获取当前持仓"""
    executor = get_executor()
    positions = executor.get_positions()
    return {"data": positions, "count": len(positions)}


@router.get("/trades")
async def get_trades(limit: int = Query(100)):
    """获取交易记录"""
    store = get_store()
    df = store.get_trades(limit)
    if df.is_empty():
        return {"data": [], "count": 0}
    return {"data": df.to_dicts(), "count": len(df)}


@router.get("/pnl")
async def get_pnl():
    """获取盈亏统计"""
    executor = get_executor()
    return executor.get_pnl_summary()
