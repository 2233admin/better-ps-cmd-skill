"""可转债 API"""

from fastapi import APIRouter, Query

from ..data.tdx_realtime import get_tdx_engine
from ..data.akshare_feed import akshare_feed

router = APIRouter(prefix="/bond", tags=["bond"])


@router.get("/list")
async def get_bond_list():
    """获取可转债列表（含转股溢价率等指标）"""
    df = akshare_feed.get_bond_list()
    if df.is_empty():
        return {"data": [], "count": 0}
    return {"data": df.to_dicts(), "count": len(df)}


@router.get("/quotes")
async def get_bond_quotes():
    """获取可转债实时行情"""
    engine = get_tdx_engine()
    bonds = engine.get_bond_list()
    quotes = engine.get_bond_quotes(bonds)
    return {"data": quotes, "count": len(quotes)}


@router.get("/history/{code}")
async def get_bond_history(code: str):
    """获取可转债历史行情"""
    df = akshare_feed.get_bond_history(code)
    if df.is_empty():
        return {"data": [], "count": 0}
    return {"data": df.to_dicts(), "count": len(df)}
