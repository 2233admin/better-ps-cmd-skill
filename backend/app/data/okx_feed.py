"""OKX 数字货币行情接入 - 对接 GPU 因子引擎

OKX 公开 API，无需 API Key:
- GET /api/v5/market/tickers?instType=SPOT  全量现货行情
- GET /api/v5/market/ticker?instId=BTC-USDT 单币对行情

数据映射到 GPU 因子引擎的 quote 格式:
  price, open, high, low, last_close, vol, cur_vol, amount, bid1, ask1
"""

import asyncio
import time

import requests
from loguru import logger


OKX_BASE = "https://www.okx.com"

# 主流 USDT 交易对 (按市值排序)
TOP_PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT",
    "MATIC-USDT", "UNI-USDT", "ATOM-USDT", "LTC-USDT", "FIL-USDT",
    "APT-USDT", "ARB-USDT", "OP-USDT", "NEAR-USDT", "INJ-USDT",
    "SUI-USDT", "SEI-USDT", "TIA-USDT", "PEPE-USDT", "WIF-USDT",
    "ORDI-USDT", "STX-USDT", "IMX-USDT", "RNDR-USDT", "FET-USDT",
    "AAVE-USDT", "MKR-USDT", "CRV-USDT", "DYDX-USDT", "SNX-USDT",
    "SAND-USDT", "MANA-USDT", "AXS-USDT", "GMT-USDT", "GALA-USDT",
    "1INCH-USDT", "SUSHI-USDT", "COMP-USDT", "ENS-USDT", "LDO-USDT",
    "RPL-USDT", "SSV-USDT", "PENDLE-USDT", "JTO-USDT", "PYTH-USDT",
]


def fetch_all_spot_tickers() -> list[dict]:
    """获取 OKX 全量现货行情"""
    try:
        r = requests.get(
            f"{OKX_BASE}/api/v5/market/tickers",
            params={"instType": "SPOT"},
            timeout=10,
        )
        data = r.json()
        if data.get("code") != "0":
            logger.error(f"OKX API error: {data.get('msg')}")
            return []
        return data.get("data", [])
    except Exception as e:
        logger.error(f"OKX fetch error: {e}")
        return []


def fetch_usdt_tickers(pairs: list[str] | None = None) -> list[dict]:
    """获取 USDT 交易对行情，过滤并排序"""
    all_tickers = fetch_all_spot_tickers()
    if not all_tickers:
        return []

    # 过滤 USDT 对
    usdt_tickers = [t for t in all_tickers if t["instId"].endswith("-USDT")]

    if pairs:
        pair_set = set(pairs)
        usdt_tickers = [t for t in usdt_tickers if t["instId"] in pair_set]

    # 按24h成交额排序
    usdt_tickers.sort(
        key=lambda t: float(t.get("volCcy24h", 0)),
        reverse=True,
    )
    return usdt_tickers


def okx_to_quotes(tickers: list[dict]) -> list[dict]:
    """将 OKX ticker 转换为 GPU 因子引擎的 quote 格式

    映射:
    - code: instId (如 "BTC-USDT")
    - price: last (最新价)
    - open: sodUtc8 (UTC+8 零点价 = A股逻辑的"开盘价")
    - high/low: high24h/low24h
    - last_close: sodUtc8 (当日开盘价作为昨收)
    - vol: vol24h (24h成交量，币本位)
    - amount: volCcy24h (24h成交额，USDT)
    - cur_vol: lastSz (最新成交量)
    - bid1/ask1: bidPx/askPx
    """
    quotes = []
    for t in tickers:
        try:
            last = float(t.get("last", 0))
            if last <= 0:
                continue

            sod = float(t.get("sodUtc8", 0)) or last
            quotes.append({
                "code": t["instId"],
                "price": last,
                "open": sod,
                "high": float(t.get("high24h", last)),
                "low": float(t.get("low24h", last)),
                "last_close": sod,
                "vol": int(float(t.get("vol24h", 0))),
                "cur_vol": int(float(t.get("lastSz", 0))),
                "amount": float(t.get("volCcy24h", 0)),
                "bid1": float(t.get("bidPx", 0)),
                "ask1": float(t.get("askPx", 0)),
                "market": 99,  # 标记为 crypto
            })
        except (ValueError, KeyError):
            continue

    return quotes


async def okx_realtime_loop(
    engine,
    interval: float = 2.0,
    pairs: list[str] | None = None,
    on_scan: callable = None,
):
    """OKX 实时行情循环 - 定时拉取并灌入 GPU 因子引擎

    Args:
        engine: GPUFactorEngine 实例
        interval: 拉取间隔(秒)，OKX 限频 20次/2秒
        pairs: 指定交易对，None=全量USDT对
        on_scan: 扫描回调 fn(opportunities_dict)
    """
    logger.info(f"Starting OKX realtime loop (interval={interval}s)")
    tick_count = 0

    while True:
        try:
            t0 = time.perf_counter()
            tickers = fetch_usdt_tickers(pairs)
            quotes = okx_to_quotes(tickers)
            t_fetch = (time.perf_counter() - t0) * 1000

            if quotes:
                engine.update(quotes)
                engine.compute()
                tick_count += 1

                if tick_count % 10 == 0:
                    logger.info(
                        f"OKX tick #{tick_count}: {len(quotes)} pairs, "
                        f"fetch={t_fetch:.0f}ms, "
                        f"compute={engine.get_perf_stats()['avg_ms']:.1f}ms"
                    )

                if on_scan and tick_count >= 15:
                    opps = engine.scan_t_opportunities()
                    total = sum(len(v) for v in opps.values())
                    if total > 0:
                        on_scan(opps)

            await asyncio.sleep(interval)
        except Exception as e:
            logger.error(f"OKX loop error: {e}")
            await asyncio.sleep(5)
