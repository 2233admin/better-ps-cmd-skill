"""AI 预测模块 - 实时推理

双 GPU 策略:
  - 有 ONNX 模型 → 核显 DirectML 推理 (不占 5090)
  - 有 PyTorch 模型 → 5090 CUDA 推理
  - 无模型 → 规则引擎 (CPU)
"""

from pathlib import Path

import numpy as np
from loguru import logger


class Predictor:
    """AI 预测器 — 支持 ONNX (核显) / PyTorch (5090) / 规则引擎"""

    def __init__(self, model_path: str = "./models"):
        self.model_path = Path(model_path)
        self.model = None
        self.onnx_session = None
        self.device = "cpu"
        self._last_factors = {}
        self._init_model()

    def _init_model(self):
        """按优先级加载模型: ONNX (核显) > PyTorch (5090) > 规则"""
        onnx_file = self.model_path / "tft_model.onnx"
        pt_file = self.model_path / "tft_model.pt"

        # 优先 ONNX → 跑核显 DirectML
        if onnx_file.exists():
            try:
                from ..data.device_manager import device_mgr
                self.onnx_session = device_mgr.get_onnx_session(onnx_file)
                self.device = "directml"
                logger.info(f"[Predictor] ONNX model on AMD iGPU: {onnx_file}")
                return
            except Exception as e:
                logger.warning(f"[Predictor] ONNX load failed: {e}")

        # 其次 PyTorch → 跑 5090 CUDA
        if pt_file.exists():
            try:
                import torch
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
                self.model = torch.load(pt_file, map_location=self.device)
                self.model.eval()
                logger.info(f"[Predictor] PyTorch model on {self.device}: {pt_file}")
                return
            except Exception as e:
                logger.warning(f"[Predictor] PyTorch load failed: {e}")

        logger.info("[Predictor] No model found, using rule-based predictions")

    def export_to_onnx(self):
        """将 PyTorch 模型导出为 ONNX，下次启动自动用核显跑"""
        if self.model is None:
            logger.error("No PyTorch model to export")
            return False
        try:
            from ..data.device_manager import device_mgr
            save_path = self.model_path / "tft_model.onnx"
            # TFT 输入: (batch, seq_len=60, n_features=6)
            device_mgr.torch_to_onnx(
                self.model.cpu(), (1, 60, 6), save_path
            )
            logger.info(f"[Predictor] Exported ONNX → 下次启动将用核显推理")
            return True
        except Exception as e:
            logger.error(f"ONNX export failed: {e}")
            return False

    def predict(self, code: str) -> dict:
        """预测价格方向"""
        if self.onnx_session is not None:
            return self._onnx_predict(code)
        if self.model is not None:
            return self._model_predict(code)
        return self._rule_predict(code)

    def _onnx_predict(self, code: str) -> dict:
        """ONNX 推理 — 跑在 AMD 核显上"""
        try:
            # 获取特征数据
            features = self._get_features(code)
            if features is None:
                return self._rule_predict(code)

            input_data = features.astype(np.float32).reshape(1, 60, 6)
            result = self.onnx_session.run(None, {"input": input_data})
            probs = result[0][0]  # (pred_len, 3)

            # 取第一步预测
            up_prob, down_prob, neutral_prob = probs[0]
            if up_prob > down_prob and up_prob > neutral_prob:
                direction = "up"
                confidence = float(up_prob)
            elif down_prob > up_prob and down_prob > neutral_prob:
                direction = "down"
                confidence = float(down_prob)
            else:
                direction = "neutral"
                confidence = float(neutral_prob)

            return {
                "code": code,
                "direction": direction,
                "confidence": round(confidence, 3),
                "predicted_change": round((up_prob - down_prob) * 0.02, 4),
                "model": "tft_onnx_directml",
                "status": "ok",
            }
        except Exception as e:
            logger.error(f"ONNX predict error: {e}")
            return self._rule_predict(code)

    def _model_predict(self, code: str) -> dict:
        """PyTorch 推理 — 跑在 5090 CUDA 上"""
        try:
            import torch
            features = self._get_features(code)
            if features is None:
                return self._rule_predict(code)

            x = torch.FloatTensor(features).unsqueeze(0).to(self.device)
            with torch.no_grad():
                probs = self.model(x)[0].cpu().numpy()

            up_prob, down_prob, neutral_prob = probs[0]
            if up_prob > down_prob and up_prob > neutral_prob:
                direction = "up"
                confidence = float(up_prob)
            elif down_prob > up_prob and down_prob > neutral_prob:
                direction = "down"
                confidence = float(down_prob)
            else:
                direction = "neutral"
                confidence = float(neutral_prob)

            return {
                "code": code,
                "direction": direction,
                "confidence": round(confidence, 3),
                "predicted_change": round((up_prob - down_prob) * 0.02, 4),
                "model": "tft_cuda",
                "status": "ok",
            }
        except Exception as e:
            logger.error(f"Model predict error: {e}")
            return self._rule_predict(code)

    def _get_features(self, code: str) -> np.ndarray | None:
        """获取 60 bar × 6 features 的输入数据"""
        try:
            from ..data.tdx_realtime import get_tdx_engine, MARKET_SH, MARKET_SZ
            market = MARKET_SH if code.startswith(("6", "11")) else MARKET_SZ
            engine = get_tdx_engine()
            bars = engine.get_kline(market, code, category=9, start=0, count=60)
            if not bars or len(bars) < 60:
                return None

            features = np.array([
                [b["open"], b["high"], b["low"], b["close"], b["volume"],
                 b.get("amount", b["close"] * b["volume"])]
                for b in bars[-60:]
            ], dtype=np.float32)

            # 标准化
            means = features.mean(axis=0, keepdims=True)
            stds = features.std(axis=0, keepdims=True)
            stds[stds == 0] = 1
            return (features - means) / stds
        except Exception:
            return None

    def _rule_predict(self, code: str) -> dict:
        """基于规则+技术因子的预测（无模型时的备用方案）"""
        from ..data.tdx_realtime import get_tdx_engine, MARKET_SH, MARKET_SZ
        from ..strategy.factors import calc_rsi, calc_macd, calc_bollinger, calc_ma
        import polars as pl

        market = MARKET_SH if code.startswith(("6", "11")) else MARKET_SZ
        engine = get_tdx_engine()

        bars = engine.get_kline(market, code, category=9, start=0, count=60)
        if not bars or len(bars) < 20:
            return {
                "code": code, "direction": "neutral", "confidence": 0.0,
                "predicted_change": 0.0, "model": "rule_based", "status": "insufficient_data",
            }

        df = pl.DataFrame(bars)
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                return {
                    "code": code, "direction": "neutral", "confidence": 0.0,
                    "predicted_change": 0.0, "model": "rule_based", "status": "missing_columns",
                }

        df = calc_rsi(df)
        df = calc_macd(df)
        df = calc_bollinger(df)
        df = calc_ma(df, [5, 20])

        last = df.row(-1, named=True)
        score = 0.0
        factors = {}

        # RSI
        rsi = last.get("rsi")
        if rsi is not None and not np.isnan(rsi):
            factors["rsi"] = round(rsi, 1)
            if rsi < 30:
                score += 0.3
            elif rsi > 70:
                score -= 0.3
            elif rsi < 45:
                score += 0.1
            elif rsi > 55:
                score -= 0.1

        # MACD
        macd_hist = last.get("macd_hist")
        if macd_hist is not None and not np.isnan(macd_hist):
            factors["macd_hist"] = round(macd_hist, 4)
            if len(df) >= 2:
                prev_hist = df.row(-2, named=True).get("macd_hist", 0)
                if prev_hist and not np.isnan(prev_hist):
                    if macd_hist > 0 and macd_hist > prev_hist:
                        score += 0.2
                    elif macd_hist < 0 and macd_hist < prev_hist:
                        score -= 0.2

        # 布林带
        bb_upper = last.get("bb_upper")
        bb_lower = last.get("bb_lower")
        close = last.get("close", 0)
        if bb_upper and bb_lower and close and not np.isnan(bb_upper):
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_pos = (close - bb_lower) / bb_range
                factors["bb_position"] = round(bb_pos, 2)
                if bb_pos < 0.2:
                    score += 0.2
                elif bb_pos > 0.8:
                    score -= 0.2

        # MA 趋势
        ma5 = last.get("ma5")
        ma20 = last.get("ma20")
        if ma5 and ma20 and not np.isnan(ma5) and not np.isnan(ma20):
            factors["ma5"] = round(ma5, 3)
            factors["ma20"] = round(ma20, 3)
            if ma5 > ma20:
                score += 0.15
            else:
                score -= 0.15

        score = max(-1.0, min(1.0, score))
        confidence = min(0.9, abs(score) * 1.2 + 0.1)
        direction = "up" if score > 0.1 else "down" if score < -0.1 else "neutral"
        predicted_change = score * 0.02

        self._last_factors = factors

        return {
            "code": code,
            "direction": direction,
            "confidence": round(confidence, 3),
            "predicted_change": round(predicted_change, 4),
            "model": "rule_based",
            "status": "ok",
        }

    def get_factors(self, code: str) -> dict:
        if not self._last_factors:
            self.predict(code)
        return {
            "code": code,
            "factors": self._last_factors,
            "status": "ok" if self._last_factors else "no_data",
        }

    def get_status(self) -> dict:
        return {
            "model_loaded": self.model is not None or self.onnx_session is not None,
            "backend": self.device,
            "model_path": str(self.model_path),
            "onnx_active": self.onnx_session is not None,
        }


_predictor: Predictor | None = None


def get_predictor() -> Predictor:
    global _predictor
    if _predictor is None:
        _predictor = Predictor()
    return _predictor
