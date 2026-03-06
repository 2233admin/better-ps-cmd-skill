"""技术指标计算 — 基于 MyTT，兼容通达信公式

用法:
    from .indicators import calc_indicators
    result = calc_indicators(kline_data, indicators=["MACD", "KDJ", "BOLL"])
"""

import numpy as np
from loguru import logger


def calc_indicators(
    kline: list[dict],
    indicators: list[str] | None = None,
) -> dict[str, dict]:
    """对K线数据批量计算技术指标

    Args:
        kline: K线数据列表，每条需含 open/close/high/low/vol
        indicators: 要计算的指标列表，默认全算

    Returns:
        {"MACD": {"DIF": [...], "DEA": [...], "MACD": [...]}, ...}
    """
    if not kline or len(kline) < 5:
        return {}

    try:
        from MyTT import (
            MACD, KDJ, RSI, BOLL, WR, CCI, ATR, OBV, MFI,
            MA, EMA, BIAS, PSY, TRIX, ROC, BBI,
        )
    except ImportError:
        logger.error("MyTT not installed: pip install MyTT")
        return {}

    CLOSE = np.array([float(k.get("close", k.get("price", 0))) for k in kline])
    HIGH = np.array([float(k.get("high", 0)) for k in kline])
    LOW = np.array([float(k.get("low", 0)) for k in kline])
    VOL = np.array([float(k.get("vol", k.get("volume", 0))) for k in kline])

    if indicators is None:
        indicators = ["MACD", "KDJ", "RSI", "BOLL", "MA", "ATR"]

    result = {}
    _to_list = lambda x: x.tolist() if hasattr(x, "tolist") else list(x)

    for name in indicators:
        try:
            if name == "MACD":
                dif, dea, macd = MACD(CLOSE)
                result["MACD"] = {"DIF": _to_list(dif), "DEA": _to_list(dea), "MACD": _to_list(macd)}
            elif name == "KDJ":
                k, d, j = KDJ(CLOSE, HIGH, LOW)
                result["KDJ"] = {"K": _to_list(k), "D": _to_list(d), "J": _to_list(j)}
            elif name == "RSI":
                rsi6 = RSI(CLOSE, 6)
                rsi12 = RSI(CLOSE, 12)
                rsi24 = RSI(CLOSE, 24)
                result["RSI"] = {"RSI6": _to_list(rsi6), "RSI12": _to_list(rsi12), "RSI24": _to_list(rsi24)}
            elif name == "BOLL":
                upper, mid, lower = BOLL(CLOSE)
                result["BOLL"] = {"UPPER": _to_list(upper), "MID": _to_list(mid), "LOWER": _to_list(lower)}
            elif name == "MA":
                result["MA"] = {
                    "MA5": _to_list(MA(CLOSE, 5)),
                    "MA10": _to_list(MA(CLOSE, 10)),
                    "MA20": _to_list(MA(CLOSE, 20)),
                    "MA60": _to_list(MA(CLOSE, 60)),
                }
            elif name == "EMA":
                result["EMA"] = {
                    "EMA12": _to_list(EMA(CLOSE, 12)),
                    "EMA26": _to_list(EMA(CLOSE, 26)),
                }
            elif name == "ATR":
                atr = ATR(CLOSE, HIGH, LOW)
                result["ATR"] = {"ATR": _to_list(atr)}
            elif name == "WR":
                wr = WR(CLOSE, HIGH, LOW, 14)
                result["WR"] = {"WR": _to_list(wr)}
            elif name == "CCI":
                cci = CCI(CLOSE, HIGH, LOW)
                result["CCI"] = {"CCI": _to_list(cci)}
            elif name == "OBV":
                obv = OBV(CLOSE, VOL)
                result["OBV"] = {"OBV": _to_list(obv)}
            elif name == "BIAS":
                b1, b2, b3 = BIAS(CLOSE, 6, 12, 24)
                result["BIAS"] = {"BIAS6": _to_list(b1), "BIAS12": _to_list(b2), "BIAS24": _to_list(b3)}
            elif name == "ROC":
                roc, maroc = ROC(CLOSE)
                result["ROC"] = {"ROC": _to_list(roc), "MAROC": _to_list(maroc)}
            elif name == "BBI":
                bbi = BBI(CLOSE)
                result["BBI"] = {"BBI": _to_list(bbi)}
        except Exception as e:
            logger.debug(f"Indicator {name} calc error: {e}")

    return result
