"""Kronos K线预测服务 — 为唯物引擎提供第5因子

输入: 历史 OHLCV K线 (从 OKX API 获取)
输出: 未来方向信号 + 置信度 → 喂给 materialist_engine

设备策略: 优先核显 DirectML，备用 5090 CUDA
"""

import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from loguru import logger


class KronosService:
    """Kronos K线基础模型预测服务"""

    def __init__(self, model_name: str = "NeoQuasar/Kronos-small",
                 tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-base",
                 device: str = "auto"):
        self.predictor = None
        self._cache: dict = {}  # pair → {pred, ts}
        self._cache_ttl = 4 * 3600  # 4小时缓存（和引擎扫描间隔一致）
        self._init_model(model_name, tokenizer_name, device)

    def _init_model(self, model_name: str, tokenizer_name: str, device: str):
        try:
            from .kronos_model import KronosTokenizer, Kronos, KronosPredictor

            if device == "auto":
                device = "cuda:0" if torch.cuda.is_available() else "cpu"

            tokenizer = KronosTokenizer.from_pretrained(tokenizer_name)
            model = Kronos.from_pretrained(model_name)
            self.predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)
            self.device = device
            params = sum(p.numel() for p in model.parameters()) / 1e6
            logger.info(f"[Kronos] Loaded {model_name} ({params:.1f}M) on {device}")
        except Exception as e:
            logger.error(f"[Kronos] Failed to load: {e}")
            self.predictor = None

    def predict_pair(self, pair: str, bar: str = "4H",
                     lookback: int = 400, pred_len: int = 12) -> dict:
        """预测单个交易对的未来K线走势

        Returns:
            {
                "direction": "up" | "down" | "neutral",
                "confidence": 0-1,
                "predicted_change": float,  # 预测涨跌幅
                "pred_close": [...],  # 预测的 close 序列
            }
        """
        # 缓存检查
        now = time.time()
        if pair in self._cache and now - self._cache[pair]["ts"] < self._cache_ttl:
            return self._cache[pair]["pred"]

        if self.predictor is None:
            return {"direction": "neutral", "confidence": 0, "predicted_change": 0}

        try:
            # 拉历史 K 线
            bars = self._fetch_klines(pair, bar, lookback + pred_len)
            if len(bars) < lookback + pred_len:
                return {"direction": "neutral", "confidence": 0, "predicted_change": 0}

            # 准备输入
            df = pd.DataFrame(bars)
            x_df = df.iloc[:lookback][["open", "high", "low", "close", "volume", "amount"]]
            x_timestamp = pd.to_datetime(df.iloc[:lookback]["datetime"])
            y_timestamp = pd.to_datetime(df.iloc[lookback:lookback + pred_len]["datetime"])

            # Kronos 预测
            pred_df = self.predictor.predict(
                df=x_df,
                x_timestamp=x_timestamp,
                y_timestamp=y_timestamp,
                pred_len=pred_len,
                T=0.8,
                top_p=0.9,
                sample_count=3,  # 3次采样取平均
            )

            # 分析预测结果
            last_close = float(df.iloc[lookback - 1]["close"])
            pred_closes = pred_df["close"].values
            pred_final = float(pred_closes[-1])
            pred_change = (pred_final - last_close) / last_close

            # 方向和置信度
            ups = sum(1 for c in pred_closes if c > last_close)
            downs = sum(1 for c in pred_closes if c < last_close)
            total = len(pred_closes)

            if ups > downs:
                direction = "up"
                confidence = ups / total
            elif downs > ups:
                direction = "down"
                confidence = downs / total
            else:
                direction = "neutral"
                confidence = 0.5

            # 用预测路径的一致性加权置信度
            changes = [(c - last_close) / last_close for c in pred_closes]
            consistency = 1.0 - np.std(np.sign(changes))  # 方向一致性
            confidence = min(0.9, confidence * (0.5 + 0.5 * consistency))

            result = {
                "direction": direction,
                "confidence": round(float(confidence), 3),
                "predicted_change": round(float(pred_change), 4),
                "pred_close": [round(float(c), 4) for c in pred_closes],
            }

            self._cache[pair] = {"pred": result, "ts": now}
            logger.info(f"[Kronos] {pair}: {direction} conf={confidence:.2f} "
                        f"pred_change={pred_change:+.2%}")
            return result

        except Exception as e:
            logger.error(f"[Kronos] Predict {pair} failed: {e}")
            return {"direction": "neutral", "confidence": 0, "predicted_change": 0}

    def _fetch_klines(self, pair: str, bar: str, limit: int) -> list[dict]:
        """从 OKX 拉历史 K 线"""
        import requests as req
        swap_id = pair + "-SWAP" if not pair.endswith("-SWAP") else pair
        all_data = []
        after = ""

        while len(all_data) < limit:
            params = {"instId": swap_id, "bar": bar, "limit": "100"}
            if after:
                params["after"] = after
            r = req.get("https://www.okx.com/api/v5/market/candles",
                        params=params, timeout=10)
            data = r.json().get("data", [])
            if not data:
                break
            all_data.extend(data)
            after = data[-1][0]
            time.sleep(0.1)

        bars = []
        for d in reversed(all_data[:limit]):
            from datetime import datetime, timezone
            ts = int(d[0])
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            bars.append({
                "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(d[1]),
                "high": float(d[2]),
                "low": float(d[3]),
                "close": float(d[4]),
                "volume": float(d[5]),
                "amount": float(d[7]) if len(d) > 7 else 0,
            })
        return bars

    def clear_cache(self):
        self._cache.clear()

    def status(self) -> dict:
        return {
            "loaded": self.predictor is not None,
            "device": getattr(self, "device", "none"),
            "cached_pairs": list(self._cache.keys()),
        }


# 全局单例
_kronos: KronosService | None = None


def get_kronos() -> KronosService:
    global _kronos
    if _kronos is None:
        _kronos = KronosService()
    return _kronos
