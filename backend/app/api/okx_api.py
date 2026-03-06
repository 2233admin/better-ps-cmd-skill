"""OKX 加密货币交易 API + 设备管理"""

import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/okx", tags=["okx"])

# 扫描结果缓存 (4小时)
_scan_cache: dict = {"data": None, "ts": 0}
_SCAN_INTERVAL = 4 * 3600  # 4小时


# ---- 账户 ----

@router.get("/account")
async def get_account():
    """账户概览"""
    import asyncio

    def _fetch():
        from ..data.okx_client import get_okx_client
        client = get_okx_client()

        bal = client.get_balance()
        positions = client.get_positions()

        account = {}
        if bal:
            d = bal[0].get('details', [{}])[0]
            account = {
                "equity": float(d.get('eq', 0)),
                "available": float(d.get('availBal', 0)),
                "frozen": float(d.get('frozenBal', 0)),
                "upl": float(d.get('upl', 0)),
            }

        pos_list = []
        for p in positions:
            pos_list.append({
                "instId": p.get('instId', ''),
                "posSide": p.get('posSide', ''),
                "pos": p.get('pos', ''),
                "avgPx": p.get('avgPx', ''),
                "markPx": p.get('markPx', ''),
                "upl": float(p.get('upl', 0)),
                "lever": p.get('lever', ''),
                "notionalUsd": p.get('notionalUsd', ''),
            })

        r = client._get('/api/v5/trade/orders-algo-pending',
                         {'instType': 'SWAP', 'ordType': 'conditional'}, auth=True)
        algos = []
        for a in r.get('data', []):
            algos.append({
                "instId": a.get('instId', ''),
                "sz": a.get('sz', ''),
                "slTriggerPx": a.get('slTriggerPx', ''),
                "tpTriggerPx": a.get('tpTriggerPx', ''),
            })

        return {"account": account, "positions": pos_list, "algos": algos}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch)


# ---- 市场分析 ----

@router.get("/scan")
async def scan_market(force: bool = False):
    """唯物主义全市场扫描 (60秒缓存)"""
    import asyncio
    from ..strategy.materialist_engine import MaterialistEngine

    import json as _json
    from pathlib import Path as _Path

    scan_file = _Path("D:/projects/quant-terminal/data/last_scan.json")
    now = time.time()

    # 从内存缓存读
    if not force and _scan_cache["data"] and now - _scan_cache["ts"] < _SCAN_INTERVAL:
        return _scan_cache["data"]

    # 从文件缓存读（重启后恢复）
    if not force and not _scan_cache["data"] and scan_file.exists():
        try:
            saved = _json.loads(scan_file.read_text(encoding='utf-8'))
            if now - saved.get("_ts", 0) < _SCAN_INTERVAL:
                _scan_cache["data"] = saved
                _scan_cache["ts"] = saved.get("_ts", 0)
                return saved
        except Exception:
            pass

    # 在线程池中运行同步扫描，避免阻塞 event loop
    def _do_scan():
        engine = MaterialistEngine(capital=50, leverage=20)
        conditions = engine.scan_market()
        # 直接用已扫描的结果生成 plans，不重新扫描
        plans = []
        for mc in conditions:
            if mc.signal == "WAIT":
                continue
            sizing = engine.calc_position_size(mc)
            plans.append({
                "pair": mc.pair, "signal": mc.signal,
                "confidence": round(mc.confidence, 2), "price": mc.price,
                "reason": mc.reason, "contradictions": mc.contradictions,
                "conditions": {
                    "trend_4h": mc.trend_4h, "vwap_dev": f"{mc.vwap_dev*100:+.2f}%",
                    "amplitude": f"{mc.amplitude*100:.1f}%",
                    "funding_rate": f"{mc.funding_rate*100:+.4f}%",
                    "taker_ratio": round(mc.taker_ratio, 2),
                    "ls_ratio": round(mc.ls_ratio, 2),
                    "pos_in_range": f"{mc.pos_in_range:.0%}",
                    "vol_trend": mc.vol_trend,
                },
                "sizing": sizing,
                "levels": {
                    "support": mc.support, "resistance": mc.resistance,
                    "stop_loss": round(mc.price * (1 - sizing["stop_loss_pct"]/100), 4)
                                if mc.signal == "LONG"
                                else round(mc.price * (1 + sizing["stop_loss_pct"]/100), 4),
                    "take_profit": round(mc.price * (1 + sizing["take_profit_pct"]/100), 4)
                                   if mc.signal == "LONG"
                                   else round(mc.price * (1 - sizing["take_profit_pct"]/100), 4),
                },
            })
        plans.sort(key=lambda x: x["confidence"], reverse=True)

        market = []
        for mc in conditions:
            market.append({
                "pair": mc.pair, "price": mc.price,
                "change": round(mc.change * 100, 2),
                "amplitude": round(mc.amplitude * 100, 1),
                "vwapDev": round(mc.vwap_dev * 100, 2),
                "fundingRate": round(mc.funding_rate * 100, 4),
                "takerRatio": round(mc.taker_ratio, 2),
                "lsRatio": round(mc.ls_ratio, 2),
                "basis": round(mc.basis * 100, 4),
                "trend4h": mc.trend_4h, "volTrend": mc.vol_trend,
                "posInRange": round(mc.pos_in_range * 100),
                "signal": mc.signal, "confidence": round(mc.confidence * 100),
                "reason": mc.reason, "contradictions": mc.contradictions,
                "support": mc.support, "resistance": mc.resistance,
            })
        return {"market": market, "plans": plans}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _do_scan)
    result["_ts"] = time.time()
    _scan_cache["data"] = result
    _scan_cache["ts"] = time.time()

    # 持久化到文件
    scan_file.parent.mkdir(parents=True, exist_ok=True)
    scan_file.write_text(_json.dumps(result, ensure_ascii=False), encoding='utf-8')

    return result


# ---- 实时行情 ----

# 行情缓存 (2秒刷新)
_tickers_cache: dict = {"data": None, "ts": 0}
_TICKER_PAIRS = [
    'ETH-USDT', 'SOL-USDT', 'BTC-USDT', 'DOGE-USDT',
    'SUI-USDT', 'PEPE-USDT', 'AVAX-USDT', 'LINK-USDT',
]

@router.get("/tickers")
async def get_tickers():
    """批量获取所有监控币种行情 (2秒缓存，极快)"""
    import asyncio
    now = time.time()
    if _tickers_cache["data"] and now - _tickers_cache["ts"] < 2:
        return _tickers_cache["data"]

    def _fetch():
        import requests as req
        # OKX 支持批量获取所有 SPOT tickers，一个请求搞定
        r = req.get('https://www.okx.com/api/v5/market/tickers',
                     params={'instType': 'SWAP'}, timeout=5)
        all_tickers = r.json().get('data', [])
        pairs_set = {p + '-SWAP' for p in _TICKER_PAIRS}
        result = []
        for t in all_tickers:
            if t['instId'] in pairs_set:
                last = float(t.get('last', 0))
                sod = float(t.get('sodUtc8', 0)) or last
                change = (last - sod) / sod * 100 if sod else 0
                result.append({
                    "instId": t['instId'],
                    "pair": t['instId'].replace('-SWAP', ''),
                    "last": last,
                    "askPx": float(t.get('askPx', 0)),
                    "bidPx": float(t.get('bidPx', 0)),
                    "high24h": float(t.get('high24h', 0)),
                    "low24h": float(t.get('low24h', 0)),
                    "vol24h": float(t.get('volCcy24h', 0)),
                    "change": round(change, 2),
                })
        result.sort(key=lambda x: abs(x['change']), reverse=True)
        return {"tickers": result, "ts": time.time()}

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _fetch)
    _tickers_cache["data"] = data
    _tickers_cache["ts"] = time.time()
    return data


@router.get("/ticker/{inst_id}")
async def get_ticker(inst_id: str):
    """获取单币种实时行情"""
    import asyncio

    def _fetch():
        from ..data.okx_client import get_okx_client
        client = get_okx_client()
        swap_id = inst_id if inst_id.endswith('-SWAP') else inst_id + '-SWAP'
        t = client.get_ticker(swap_id)
        return {
            "instId": swap_id,
            "last": float(t.get('last', 0)),
            "askPx": float(t.get('askPx', 0)),
            "bidPx": float(t.get('bidPx', 0)),
            "high24h": float(t.get('high24h', 0)),
            "low24h": float(t.get('low24h', 0)),
            "vol24h": float(t.get('volCcy24h', 0)),
        }

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch)


# ---- K线数据 ----

@router.get("/kline/{pair}")
async def get_kline(pair: str, bar: str = "1m", limit: int = 200):
    """获取 OKX K线数据，返回统一格式"""
    import asyncio

    def _fetch():
        import requests as req
        swap_id = pair if pair.endswith('-SWAP') else pair + '-SWAP'
        r = req.get('https://www.okx.com/api/v5/market/candles',
                     params={'instId': swap_id, 'bar': bar, 'limit': str(limit)},
                     timeout=10)
        raw = r.json().get('data', [])
        # OKX 返回 [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        # 时间倒序，需要反转
        data = []
        for item in reversed(raw):
            ts_ms = int(item[0])
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            data.append({
                "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "amount": float(item[7]) if len(item) > 7 else 0,
            })
        return {"data": data, "count": len(data)}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch)


# ---- 信号日志 ----

@router.get("/signals")
async def get_signals(limit: int = 20):
    """获取历史信号"""
    import json
    from pathlib import Path
    log = Path("D:/projects/quant-terminal/data/signals.jsonl")
    if not log.exists():
        return {"signals": []}
    lines = log.read_text(encoding='utf-8').strip().split('\n')
    signals = [json.loads(l) for l in lines[-limit:]]
    signals.reverse()
    return {"signals": signals}


# ---- 交易 ----

class TradeRequest(BaseModel):
    pair: str
    side: str  # "buy" or "sell"
    posSide: str  # "long" or "short"
    size: str
    price: str | None = None
    ordType: str = "limit"  # "limit" or "market"
    lever: int = 20


@router.post("/trade")
async def execute_trade(req: TradeRequest):
    """执行交易"""
    from ..data.okx_client import get_okx_client
    client = get_okx_client()

    swap_id = req.pair if req.pair.endswith('-SWAP') else req.pair + '-SWAP'

    # 设置杠杆
    client._post('/api/v5/account/set-leverage', {
        'instId': swap_id, 'lever': str(req.lever), 'mgnMode': 'cross',
    })

    # 下单
    params = {
        'instId': swap_id,
        'tdMode': 'cross',
        'side': req.side,
        'posSide': req.posSide,
        'ordType': req.ordType,
        'sz': req.size,
    }
    if req.price and req.ordType == 'limit':
        params['px'] = req.price

    result = client._post('/api/v5/trade/order', params)

    if result.get('code') != '0':
        raise HTTPException(400, f"Order failed: {result}")

    return {"orderId": result['data'][0]['ordId'], "status": "placed"}


class AlgoRequest(BaseModel):
    instId: str
    posSide: str
    size: str
    slPrice: str | None = None
    tpPrice: str | None = None


@router.post("/set-sl-tp")
async def set_sl_tp(req: AlgoRequest):
    """设置止损止盈"""
    from ..data.okx_client import get_okx_client
    client = get_okx_client()

    close_side = 'sell' if req.posSide == 'long' else 'buy'
    results = []

    if req.slPrice:
        r = client._post('/api/v5/trade/order-algo', {
            'instId': req.instId, 'tdMode': 'cross',
            'side': close_side, 'posSide': req.posSide,
            'ordType': 'conditional', 'sz': req.size,
            'slTriggerPx': req.slPrice, 'slOrdPx': '-1',
            'slTriggerPxType': 'last',
        })
        results.append({"type": "SL", "code": r.get('code')})

    if req.tpPrice:
        r = client._post('/api/v5/trade/order-algo', {
            'instId': req.instId, 'tdMode': 'cross',
            'side': close_side, 'posSide': req.posSide,
            'ordType': 'conditional', 'sz': req.size,
            'tpTriggerPx': req.tpPrice, 'tpOrdPx': '-1',
            'tpTriggerPxType': 'last',
        })
        results.append({"type": "TP", "code": r.get('code')})

    return {"results": results}


class CloseRequest(BaseModel):
    instId: str
    posSide: str
    size: str


@router.post("/close")
async def close_position(req: CloseRequest):
    """市价平仓"""
    from ..data.okx_client import get_okx_client
    client = get_okx_client()

    close_side = 'sell' if req.posSide == 'long' else 'buy'
    result = client._post('/api/v5/trade/order', {
        'instId': req.instId, 'tdMode': 'cross',
        'side': close_side, 'posSide': req.posSide,
        'ordType': 'market', 'sz': req.size,
    })

    if result.get('code') != '0':
        raise HTTPException(400, f"Close failed: {result}")
    return {"status": "closed", "orderId": result['data'][0]['ordId']}


# ---- 一键交易 (根据分析计划) ----

class QuickTradeRequest(BaseModel):
    planIndex: int = 0  # 交易计划序号


@router.post("/quick-trade")
async def quick_trade(req: QuickTradeRequest):
    """根据唯物分析结果一键下单"""
    import requests as http_requests
    from ..strategy.materialist_engine import MaterialistEngine
    from ..data.okx_client import get_okx_client

    client = get_okx_client()
    engine = MaterialistEngine(capital=50, leverage=20)
    plans = engine.get_trading_plan()

    if req.planIndex >= len(plans):
        raise HTTPException(400, f"Plan index {req.planIndex} out of range (have {len(plans)})")

    plan = plans[req.planIndex]
    pair = plan['pair']
    signal = plan['signal']
    swap_id = pair + '-SWAP'
    side = 'buy' if signal == 'LONG' else 'sell'
    pos_side = 'long' if signal == 'LONG' else 'short'
    close_side = 'sell' if signal == 'LONG' else 'buy'

    # 合约规格
    r = http_requests.get('https://www.okx.com/api/v5/public/instruments',
                          params={'instType': 'SWAP', 'instId': swap_id}, timeout=10)
    spec = r.json().get('data', [{}])[0]
    ct_val = float(spec.get('ctVal', '1'))
    min_sz = float(spec.get('minSz', '1'))

    # 计算下单量
    notional = plan['sizing']['notional']
    price = plan['price']
    size = notional / (ct_val * price)
    size = max(min_sz, round(size / min_sz) * min_sz)
    size_str = f"{size:g}"

    # 杠杆
    client._post('/api/v5/account/set-leverage', {
        'instId': swap_id, 'lever': '20', 'mgnMode': 'cross',
    })

    # 取最新价
    t = client.get_ticker(swap_id)
    exec_price = float(t['askPx']) if side == 'buy' else float(t['bidPx'])

    # 下单
    result = client._post('/api/v5/trade/order', {
        'instId': swap_id, 'tdMode': 'cross',
        'side': side, 'posSide': pos_side,
        'ordType': 'limit', 'sz': size_str, 'px': str(exec_price),
    })

    if result.get('code') != '0':
        raise HTTPException(400, f"Order failed: {result}")

    ord_id = result['data'][0]['ordId']

    # 止损止盈
    import time
    time.sleep(1)
    sl = plan['levels']['stop_loss']
    tp = plan['levels']['take_profit']

    client._post('/api/v5/trade/order-algo', {
        'instId': swap_id, 'tdMode': 'cross',
        'side': close_side, 'posSide': pos_side,
        'ordType': 'conditional', 'sz': size_str,
        'slTriggerPx': str(sl), 'slOrdPx': '-1', 'slTriggerPxType': 'last',
    })
    client._post('/api/v5/trade/order-algo', {
        'instId': swap_id, 'tdMode': 'cross',
        'side': close_side, 'posSide': pos_side,
        'ordType': 'conditional', 'sz': size_str,
        'tpTriggerPx': str(tp), 'tpOrdPx': '-1', 'tpTriggerPxType': 'last',
    })

    return {
        "orderId": ord_id,
        "signal": signal,
        "pair": swap_id,
        "price": exec_price,
        "size": size_str,
        "stopLoss": sl,
        "takeProfit": tp,
        "margin": plan['sizing']['margin'],
    }
