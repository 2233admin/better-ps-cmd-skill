"""市场行情 API"""

from fastapi import APIRouter, Query

from ..data.tdx_realtime import get_tdx_engine, MARKET_SH, MARKET_SZ
from ..data.feed_manager import get_feed_manager
from ..data.akshare_feed import akshare_feed
from ..data.store import get_store

router = APIRouter(prefix="/market", tags=["market"])


def _parse_market(code: str) -> int:
    """根据代码判断市场: 6/11开头=上海, 0/3/12开头=深圳"""
    if code.startswith(("6", "11")):
        return MARKET_SH
    return MARKET_SZ


@router.get("/quotes")
async def get_quotes(codes: str = Query(..., description="逗号分隔的证券代码")):
    """批量获取实时行情 (通过 DataFeedManager 自动切换数据源)"""
    feed_mgr = get_feed_manager()
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    quotes = feed_mgr.get_quotes(code_list)
    return {"data": quotes, "count": len(quotes)}


@router.get("/kline/{code}")
async def get_kline(
    code: str,
    category: int = Query(9, description="K线类型: 7=1分钟 0=5分钟 4=日线 5=周线"),
    klt: int = Query(0, description="K线周期(优先): 5/15/30/60/101(日)/102(周)"),
    count: int = Query(800, description="数据条数"),
    start: int = Query(0, description="起始位置"),
):
    """获取K线数据 (通过 DataFeedManager 自动切换)

    klt 参数优先；若 klt=0 则回退到 category (兼容旧接口)
    """
    if klt > 0:
        feed_mgr = get_feed_manager()
        data = feed_mgr.get_kline(code, klt, count)
        market = _parse_market(code)
    else:
        engine = get_tdx_engine()
        market = _parse_market(code)
        data = engine.get_kline(market, code, category, start, count)
    return {"data": data, "count": len(data), "code": code, "market": market}


@router.get("/tick/{code}")
async def get_tick(
    code: str,
    count: int = Query(2000, description="数据条数"),
):
    """获取逐笔成交"""
    engine = get_tdx_engine()
    market = _parse_market(code)
    data = engine.get_transaction_data(market, code, 0, count)
    return {"data": data, "count": len(data)}


@router.get("/minute/{code}")
async def get_minute(code: str):
    """获取当日分时数据"""
    engine = get_tdx_engine()
    market = _parse_market(code)
    data = engine.get_minute_data(market, code)
    return {"data": data, "count": len(data)}


@router.get("/securities")
async def get_securities(
    market: int = Query(0, description="市场: 0=深圳 1=上海"),
    start: int = Query(0),
    count: int = Query(100),
):
    """获取证券列表"""
    engine = get_tdx_engine()
    data = engine.get_security_list(market, start)
    return {"data": data[:count], "total": len(data)}


@router.post("/import-kline/{code}")
async def import_kline(code: str, count: int = Query(800, description="导入条数")):
    """从TDX拉取历史日线并存入DuckDB"""
    engine = get_tdx_engine()
    store = get_store()
    market = _parse_market(code)
    data = engine.get_kline(market, code, category=9, start=0, count=count)
    if not data:
        return {"status": "error", "message": f"No data for {code}"}

    # 转换字段名 (pytdx 返回 datetime，DuckDB 需要 date)
    for row in data:
        if "datetime" in row and "date" not in row:
            row["date"] = str(row["datetime"]).split(" ")[0]

    store.save_kline_daily(code, market, data)
    return {"status": "ok", "code": code, "market": market, "count": len(data)}


@router.get("/indicators/{code}")
async def get_indicators(
    code: str,
    klt: int = Query(101, description="K线周期: 5/15/30/60/101(日)"),
    count: int = Query(120, description="K线条数"),
    indicators: str = Query("MACD,KDJ,RSI,BOLL,MA", description="逗号分隔的指标列表"),
):
    """获取技术指标 (基于 MyTT)"""
    from ..data.indicators import calc_indicators
    feed_mgr = get_feed_manager()
    kline = feed_mgr.get_kline(code, klt, count)
    if not kline:
        return {"data": {}, "error": "no kline data"}
    ind_list = [s.strip().upper() for s in indicators.split(",") if s.strip()]
    result = calc_indicators(kline, ind_list)
    return {"data": result, "kline_count": len(kline), "indicators": list(result.keys())}


@router.get("/capital-flow/{code}")
async def get_capital_flow(
    code: str,
    days: int = Query(30, description="天数"),
):
    """个股资金流向 (adata)"""
    from ..data.http_feed import AdataFeed
    feed = AdataFeed()
    data = feed.get_capital_flow(code, days)
    return {"data": data, "count": len(data)}


@router.get("/north-flow")
async def get_north_flow():
    """北向资金流向"""
    df = akshare_feed.get_north_flow()
    if df.is_empty():
        return {"data": [], "count": 0}
    return {"data": df.to_dicts(), "count": len(df)}


@router.get("/sector-flow")
async def get_sector_flow():
    """板块资金流"""
    df = akshare_feed.get_sector_flow()
    if df.is_empty():
        return {"data": [], "count": 0}
    return {"data": df.to_dicts(), "count": len(df)}


# ---- GPU 因子引擎 API ----

@router.get("/gpu/factors/{code}")
async def get_gpu_factors(code: str):
    """获取单只票的GPU实时因子"""
    from ..data.gpu_factors import get_gpu_factor_engine
    engine = get_gpu_factor_engine()
    factors = engine.get_stock_factors(code)
    if factors is None:
        return {"error": f"No data for {code}"}
    return {"code": code, "factors": factors}


@router.get("/gpu/scan")
async def gpu_scan_opportunities():
    """GPU全市场做T机会扫描"""
    from ..data.gpu_factors import get_gpu_factor_engine
    engine = get_gpu_factor_engine()
    engine.compute()
    opps = engine.scan_t_opportunities()
    return {
        "long_t": [{"code": c, "score": s, "reason": r} for c, s, r in opps["long_t"]],
        "short_t": [{"code": c, "score": s, "reason": r} for c, s, r in opps["short_t"]],
        "scalp": [{"code": c, "score": s, "reason": r} for c, s, r in opps["scalp"]],
    }


@router.get("/gpu/perf")
async def gpu_perf():
    """GPU因子引擎性能统计"""
    from ..data.gpu_factors import get_gpu_factor_engine
    engine = get_gpu_factor_engine()
    return engine.get_perf_stats()
