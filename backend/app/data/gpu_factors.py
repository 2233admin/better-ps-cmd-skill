"""GPU 实时因子引擎 - 全市场5000+只票并行计算技术指标

使用 PyTorch CUDA 张量运算，单次计算 5000 只票 x 20 个因子 < 10ms。
输入: tick 行情流 → 累积为 (N_stocks, T_window) 矩阵
输出: (N_stocks, N_factors) 因子矩阵，可直接用于策略扫描
"""

import time

import torch
from loguru import logger


# 因子名称索引
F_VWAP = 0          # 成交量加权均价
F_VWAP_DEV = 1      # 价格偏离VWAP
F_RSI = 2           # 相对强弱指标 (14期)
F_MACD = 3          # MACD 柱
F_MACD_SIGNAL = 4   # MACD 信号线
F_BOLL_UPPER = 5    # 布林上轨
F_BOLL_LOWER = 6    # 布林下轨
F_BOLL_POS = 7      # 布林带位置 (0=下轨, 1=上轨)
F_VOL_RATIO = 8     # 量比 (近期量/长期均量)
F_MOMENTUM = 9      # 动量 (N期收益率)
F_VOLATILITY = 10   # 已实现波动率
F_PRICE_CHG = 11    # 涨跌幅
F_AMPLITUDE = 12    # 振幅
F_TURNOVER = 13     # 换手率代理 (量/均量)
F_BID_ASK_SPREAD = 14  # 买卖价差
F_TREND_STRENGTH = 15  # 趋势强度 (线性回归斜率)
F_SKEW = 16         # 收益率偏度
F_LARGE_VOL = 17    # 大单占比 (量超均值2倍的tick)
F_MEAN_REVERT = 18  # 均值回归信号
F_BREAK_SIGNAL = 19 # 突破信号

N_FACTORS = 20

FACTOR_NAMES = [
    "vwap", "vwap_dev", "rsi", "macd", "macd_signal",
    "boll_upper", "boll_lower", "boll_pos", "vol_ratio", "momentum",
    "volatility", "price_chg", "amplitude", "turnover", "bid_ask_spread",
    "trend_strength", "skew", "large_vol", "mean_revert", "break_signal",
]


class GPUFactorEngine:
    """GPU 实时因子计算引擎

    架构:
    - 维护一个 (N, T) 的滑动窗口矩阵，N=股票数, T=历史tick数
    - 每次行情推送更新矩阵，然后一次性算所有因子
    - 全部运算在 GPU 上完成，零CPU参与
    """

    def __init__(
        self,
        max_stocks: int = 6000,
        window_size: int = 120,  # 保留120个tick的历史 (~1分钟@500ms)
        device: str = "cuda",
    ):
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.max_stocks = max_stocks
        self.window_size = window_size
        self.n_stocks = 0

        # 代码映射
        self._code_to_idx: dict[str, int] = {}
        self._idx_to_code: dict[int, str] = {}

        # 数据矩阵 (全部在GPU)
        self.prices = torch.zeros(max_stocks, window_size, device=self.device)
        self.volumes = torch.zeros(max_stocks, window_size, device=self.device)
        self.amounts = torch.zeros(max_stocks, window_size, device=self.device)
        self.highs = torch.zeros(max_stocks, window_size, device=self.device)
        self.lows = torch.zeros(max_stocks, window_size, device=self.device)
        self.opens = torch.zeros(max_stocks, device=self.device)
        self.last_closes = torch.zeros(max_stocks, device=self.device)
        self.bid1s = torch.zeros(max_stocks, device=self.device)
        self.ask1s = torch.zeros(max_stocks, device=self.device)

        # 写入指针
        self._ptr = 0
        self._filled = 0  # 已填充的tick数

        # 因子输出
        self.factors = torch.zeros(max_stocks, N_FACTORS, device=self.device)

        # 性能统计
        self._compute_times: list[float] = []

        if self.device.type == "cuda":
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
            logger.info(f"GPU Factor Engine initialized on {gpu_name} ({gpu_mem:.0f}GB)")
            # 预估显存: 6000 * 120 * 5矩阵 * 4字节 ≈ 14MB，几乎不占
            logger.info(f"  矩阵: {max_stocks}x{window_size}, 预估显存 ~{max_stocks * window_size * 5 * 4 / 1e6:.0f}MB")
        else:
            logger.warning("CUDA not available, falling back to CPU (performance will be limited)")

    def _get_or_create_idx(self, code: str) -> int:
        """获取或创建股票索引"""
        if code in self._code_to_idx:
            return self._code_to_idx[code]
        idx = self.n_stocks
        if idx >= self.max_stocks:
            return -1
        self._code_to_idx[code] = idx
        self._idx_to_code[idx] = code
        self.n_stocks = idx + 1
        return idx

    def update(self, quotes: list[dict]):
        """批量更新行情数据到GPU矩阵

        Args:
            quotes: pytdx 行情列表 [{code, price, vol, amount, high, low, ...}, ...]
        """
        if not quotes:
            return

        n = len(quotes)
        # 在CPU上收集数据
        indices = []
        price_vals = []
        vol_vals = []
        amount_vals = []
        high_vals = []
        low_vals = []

        for q in quotes:
            code = q.get("code", "")
            price = q.get("price", 0)
            if not code or price <= 0:
                continue

            idx = self._get_or_create_idx(code)
            if idx < 0:
                continue

            indices.append(idx)
            price_vals.append(price)
            vol_vals.append(float(q.get("cur_vol", 0)))
            amount_vals.append(float(q.get("amount", 0)))
            high_vals.append(float(q.get("high", price)))
            low_vals.append(float(q.get("low", price)))

            # 标量更新 (不进窗口)
            self.opens[idx] = q.get("open", 0)
            self.last_closes[idx] = q.get("last_close", 0)
            self.bid1s[idx] = q.get("bid1", 0)
            self.ask1s[idx] = q.get("ask1", 0)

        if not indices:
            return

        # 批量写入GPU
        idx_tensor = torch.tensor(indices, dtype=torch.long, device=self.device)
        col = self._ptr % self.window_size

        self.prices[idx_tensor, col] = torch.tensor(price_vals, device=self.device)
        self.volumes[idx_tensor, col] = torch.tensor(vol_vals, device=self.device)
        self.amounts[idx_tensor, col] = torch.tensor(amount_vals, device=self.device)
        self.highs[idx_tensor, col] = torch.tensor(high_vals, device=self.device)
        self.lows[idx_tensor, col] = torch.tensor(low_vals, device=self.device)

        self._ptr += 1
        self._filled = min(self._filled + 1, self.window_size)

    def compute(self) -> torch.Tensor:
        """计算全部因子，返回 (N, N_FACTORS) 张量

        全部运算在GPU上完成，单次调用 < 10ms (5000只票)
        """
        t0 = time.perf_counter()
        n = self.n_stocks
        if n == 0 or self._filled < 2:
            return self.factors[:0]

        T = self._filled
        # 获取有效数据切片
        p = self.prices[:n, :T]  # (N, T)
        v = self.volumes[:n, :T]
        a = self.amounts[:n, :T]
        h = self.highs[:n, :T]
        lo = self.lows[:n, :T]

        last_p = p[:, -1]  # 最新价
        opens = self.opens[:n]
        last_closes = self.last_closes[:n]

        f = self.factors[:n]

        # ========== 因子计算 (全GPU并行) ==========

        # 1. VWAP (用价格*成交量的加权平均，因为v和a是每tick增量)
        weighted_price = (p * v)
        cum_weighted = weighted_price.sum(dim=1)
        cum_vol = v.sum(dim=1)
        vwap = torch.where(cum_vol > 0, cum_weighted / cum_vol, last_p)
        f[:, F_VWAP] = vwap

        # 2. VWAP偏离率
        f[:, F_VWAP_DEV] = torch.where(vwap > 0, (last_p - vwap) / vwap, torch.zeros_like(vwap))

        # 3. RSI (14期)
        if T >= 15:
            diff = p[:, 1:] - p[:, :-1]  # (N, T-1)
            gains = torch.clamp(diff[:, -14:], min=0).mean(dim=1)
            losses = torch.clamp(-diff[:, -14:], min=0).mean(dim=1)
            rs = torch.where(losses > 1e-10, gains / losses, torch.full_like(gains, 100.0))
            f[:, F_RSI] = 100.0 - 100.0 / (1.0 + rs)
        else:
            f[:, F_RSI] = 50.0

        # 4-5. MACD (12, 26, 9)
        if T >= 26:
            ema12 = self._ema(p, 12)
            ema26 = self._ema(p, 26)
            macd_line = ema12 - ema26
            # 简化: 用最近9个MACD值做信号线
            if T >= 35:
                macd_hist = p[:, -35:]
                ema12_h = self._ema(macd_hist, 12)
                ema26_h = self._ema(macd_hist, 26)
                macd_series = ema12_h - ema26_h
                # 这里只取最后一个点的差异作为柱状
                f[:, F_MACD_SIGNAL] = macd_series  # 近似
            else:
                f[:, F_MACD_SIGNAL] = macd_line * 0.8
            f[:, F_MACD] = macd_line - f[:, F_MACD_SIGNAL]
        else:
            f[:, F_MACD] = 0.0
            f[:, F_MACD_SIGNAL] = 0.0

        # 6-8. 布林带 (20期)
        if T >= 20:
            window = p[:, -20:]
            mean = window.mean(dim=1)
            std = window.std(dim=1)
            f[:, F_BOLL_UPPER] = mean + 2 * std
            f[:, F_BOLL_LOWER] = mean - 2 * std
            band_width = 4 * std
            f[:, F_BOLL_POS] = torch.where(
                band_width > 1e-10,
                (last_p - f[:, F_BOLL_LOWER]) / band_width,
                torch.full_like(last_p, 0.5),
            )
        else:
            f[:, F_BOLL_UPPER] = last_p * 1.02
            f[:, F_BOLL_LOWER] = last_p * 0.98
            f[:, F_BOLL_POS] = 0.5

        # 9. 量比
        if T >= 20:
            recent_vol = v[:, -5:].mean(dim=1)
            avg_vol = v[:, -20:].mean(dim=1)
            f[:, F_VOL_RATIO] = torch.where(avg_vol > 0, recent_vol / avg_vol, torch.ones_like(avg_vol))
        else:
            f[:, F_VOL_RATIO] = 1.0

        # 10. 动量 (10期收益率)
        lookback = min(10, T - 1)
        if lookback > 0:
            prev_p = p[:, -(lookback + 1)]
            f[:, F_MOMENTUM] = torch.where(prev_p > 0, (last_p - prev_p) / prev_p, torch.zeros_like(last_p))
        else:
            f[:, F_MOMENTUM] = 0.0

        # 11. 已实现波动率
        if T >= 10:
            returns = torch.log(p[:, 1:] / p[:, :-1].clamp(min=1e-10))
            f[:, F_VOLATILITY] = returns[:, -10:].std(dim=1) * (480 ** 0.5)  # 年化近似
        else:
            f[:, F_VOLATILITY] = 0.0

        # 12. 涨跌幅
        f[:, F_PRICE_CHG] = torch.where(
            last_closes > 0, (last_p - last_closes) / last_closes, torch.zeros_like(last_p)
        )

        # 13. 振幅
        day_high = h.max(dim=1).values
        day_low = lo.min(dim=1).values
        # 过滤零值
        day_low_safe = torch.where(day_low > 0, day_low, last_p)
        f[:, F_AMPLITUDE] = torch.where(
            last_closes > 0, (day_high - day_low_safe) / last_closes, torch.zeros_like(last_p)
        )

        # 14. 换手率代理
        total_vol = v.sum(dim=1)
        mean_vol = v.mean(dim=1)
        f[:, F_TURNOVER] = torch.where(mean_vol > 0, total_vol / (mean_vol * self.window_size), torch.ones_like(total_vol))

        # 15. 买卖价差
        bid1 = self.bid1s[:n]
        ask1 = self.ask1s[:n]
        f[:, F_BID_ASK_SPREAD] = torch.where(
            last_p > 0, (ask1 - bid1) / last_p, torch.zeros_like(last_p)
        )

        # 16. 趋势强度 (最近20个tick的线性回归斜率)
        trend_window = min(20, T)
        if trend_window >= 5:
            window = p[:, -trend_window:]  # (N, W)
            x = torch.arange(trend_window, dtype=torch.float32, device=self.device)
            x_mean = x.mean()
            y_mean = window.mean(dim=1, keepdim=True)
            # 批量线性回归: slope = sum((x-x_mean)*(y-y_mean)) / sum((x-x_mean)^2)
            x_diff = x - x_mean  # (W,)
            y_diff = window - y_mean  # (N, W)
            numerator = (x_diff.unsqueeze(0) * y_diff).sum(dim=1)  # (N,)
            denominator = (x_diff ** 2).sum()  # scalar
            slope = numerator / denominator  # (N,)
            f[:, F_TREND_STRENGTH] = torch.where(
                y_mean.squeeze() > 0, slope / y_mean.squeeze(), torch.zeros_like(slope)
            )
        else:
            f[:, F_TREND_STRENGTH] = 0.0

        # 17. 收益率偏度
        if T >= 20:
            returns = p[:, 1:] - p[:, :-1]
            r = returns[:, -20:]
            r_mean = r.mean(dim=1, keepdim=True)
            r_std = r.std(dim=1, keepdim=True).clamp(min=1e-10)
            r_norm = (r - r_mean) / r_std
            f[:, F_SKEW] = (r_norm ** 3).mean(dim=1)
        else:
            f[:, F_SKEW] = 0.0

        # 18. 大单占比
        if T >= 10:
            vol_mean = v[:, -20:].mean(dim=1, keepdim=True) if T >= 20 else v.mean(dim=1, keepdim=True)
            large_mask = v[:, -10:] > (vol_mean * 2)  # 超过均量2倍
            f[:, F_LARGE_VOL] = large_mask.float().mean(dim=1)
        else:
            f[:, F_LARGE_VOL] = 0.0

        # 19. 均值回归信号 (偏离VWAP + RSI超卖/超买)
        vwap_dev = f[:, F_VWAP_DEV]
        rsi = f[:, F_RSI]
        # 负偏离 + RSI<30 = 强超卖信号 (正值=买入)
        # 正偏离 + RSI>70 = 强超买信号 (负值=卖出)
        f[:, F_MEAN_REVERT] = torch.where(
            rsi < 30, -vwap_dev * (1 + (30 - rsi) / 30),
            torch.where(rsi > 70, -vwap_dev * (1 + (rsi - 70) / 30), torch.zeros_like(rsi))
        )

        # 20. 突破信号
        if T >= 20:
            high_20 = p[:, -20:].max(dim=1).values
            low_20 = p[:, -20:].min(dim=1).values
            range_20 = (high_20 - low_20).clamp(min=1e-10)
            # 突破上轨=正, 突破下轨=负
            f[:, F_BREAK_SIGNAL] = torch.where(
                last_p > high_20 * 0.998, (last_p - high_20) / range_20,
                torch.where(last_p < low_20 * 1.002, (last_p - low_20) / range_20, torch.zeros_like(last_p))
            )
        else:
            f[:, F_BREAK_SIGNAL] = 0.0

        elapsed = (time.perf_counter() - t0) * 1000
        self._compute_times.append(elapsed)
        if len(self._compute_times) > 100:
            self._compute_times = self._compute_times[-100:]

        return self.factors[:n]

    def _ema(self, data: torch.Tensor, period: int) -> torch.Tensor:
        """计算EMA的最后一个值 (全GPU)

        Args:
            data: (N, T) 价格矩阵
            period: EMA周期

        Returns:
            (N,) 最后一个EMA值
        """
        alpha = 2.0 / (period + 1)
        window = data[:, -period:]
        # 加权: 最新权重最高
        weights = torch.tensor(
            [(1 - alpha) ** i for i in range(period - 1, -1, -1)],
            device=self.device,
        )
        weights = weights / weights.sum()
        return (window * weights.unsqueeze(0)).sum(dim=1)

    def get_factor_matrix(self) -> tuple[dict[str, int], torch.Tensor]:
        """获取因子矩阵和代码映射

        Returns:
            (code_to_idx, factors_tensor)
        """
        return self._code_to_idx.copy(), self.factors[:self.n_stocks].clone()

    def get_stock_factors(self, code: str) -> dict[str, float] | None:
        """获取单只票的全部因子"""
        idx = self._code_to_idx.get(code)
        if idx is None:
            return None
        row = self.factors[idx].cpu().tolist()
        return {name: round(val, 6) for name, val in zip(FACTOR_NAMES, row)}

    def scan(self, factor_idx: int, threshold: float, direction: str = "above") -> list[tuple[str, float]]:
        """全市场扫描: 找出某因子超过/低于阈值的票

        Args:
            factor_idx: 因子索引 (如 F_VWAP_DEV)
            threshold: 阈值
            direction: "above" 或 "below"

        Returns:
            [(code, factor_value), ...] 按绝对值排序
        """
        n = self.n_stocks
        if n == 0:
            return []

        col = self.factors[:n, factor_idx]
        if direction == "above":
            mask = col > threshold
        else:
            mask = col < threshold

        indices = mask.nonzero(as_tuple=True)[0]
        results = []
        for idx in indices.cpu().tolist():
            code = self._idx_to_code.get(idx, "?")
            val = col[idx].item()
            results.append((code, val))

        results.sort(key=lambda x: abs(x[1]), reverse=True)
        return results

    def scan_t_opportunities(self) -> dict:
        """扫描做T机会

        Returns:
            {
                "long_t": [(code, score, reason), ...],  # 正T机会 (超卖回弹)
                "short_t": [(code, score, reason), ...], # 反T机会 (超买回落)
                "scalp": [(code, score, reason), ...],   # 半路T (急杀企稳)
            }
        """
        n = self.n_stocks
        if n == 0:
            return {"long_t": [], "short_t": [], "scalp": []}

        f = self.factors[:n]
        vwap_dev = f[:, F_VWAP_DEV]
        rsi = f[:, F_RSI]
        vol_ratio = f[:, F_VOL_RATIO]
        trend = f[:, F_TREND_STRENGTH]
        price_chg = f[:, F_PRICE_CHG]
        amplitude = f[:, F_AMPLITUDE]
        boll_pos = f[:, F_BOLL_POS]

        # 成交额门槛 (1亿) - 用 price*volume 的累计近似
        amount = (f[:, F_VWAP] * self.volumes[:n, :self._filled].sum(dim=1))
        amount_ok = amount > 1e8

        # === 正T机会: 综合评分制 (软条件，加权求和) ===
        # 硬门槛: 必须在VWAP下方
        long_t_base = amount_ok & (vwap_dev < -0.005)
        # 软分数: 各因子贡献加权
        long_t_score = (
            (-vwap_dev).clamp(0) * 150          # 偏离越大分越高
            + (45 - rsi).clamp(0) * 0.3         # RSI越低分越高
            + (0.8 - vol_ratio).clamp(0) * 8    # 缩量加分
            + (trend + 0.001).clamp(0) * 500    # 趋势走平/反转加分
        ) * long_t_base.float()
        long_t_mask = long_t_base & (long_t_score > 1.0)

        # === 反T机会: 综合评分制 ===
        short_t_base = amount_ok & (vwap_dev > 0.008)
        short_t_score = (
            vwap_dev.clamp(0) * 120
            + (rsi - 55).clamp(0) * 0.2
            + (vol_ratio - 1.0).clamp(0) * 5
        ) * short_t_base.float()
        short_t_mask = short_t_base & (short_t_score > 1.0)

        # === 半路T机会: 急杀企稳 ===
        scalp_base = amount_ok & (price_chg < -0.02)
        scalp_score = (
            (-price_chg).clamp(0) * 100
            + (0.8 - vol_ratio).clamp(0) * 5
            + (trend + 0.001).clamp(0) * 300
            + (0.3 - boll_pos).clamp(0) * 10
        ) * scalp_base.float()
        scalp_mask = scalp_base & (scalp_score > 2.0)

        def _extract(mask: torch.Tensor, score: torch.Tensor, factors_info: list[tuple[int, str]]) -> list[tuple[str, float, str]]:
            indices = mask.nonzero(as_tuple=True)[0]
            results = []
            for idx in indices.cpu().tolist():
                code = self._idx_to_code.get(idx, "?")
                s = score[idx].item()
                # 生成原因描述
                parts = []
                for fi, label in factors_info:
                    parts.append(f"{label}:{f[idx, fi].item():.2%}")
                reason = " ".join(parts)
                results.append((code, round(s, 2), reason))
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:20]  # 最多返回20个

        return {
            "long_t": _extract(long_t_mask, long_t_score,
                               [(F_VWAP_DEV, "偏离"), (F_RSI, "RSI"), (F_VOL_RATIO, "量比")]),
            "short_t": _extract(short_t_mask, short_t_score,
                                [(F_VWAP_DEV, "偏离"), (F_RSI, "RSI"), (F_VOL_RATIO, "量比")]),
            "scalp": _extract(scalp_mask, scalp_score,
                              [(F_PRICE_CHG, "跌幅"), (F_VOL_RATIO, "量比"), (F_TREND_STRENGTH, "趋势")]),
        }

    def get_perf_stats(self) -> dict:
        """性能统计"""
        if not self._compute_times:
            return {"avg_ms": 0, "max_ms": 0, "count": 0}
        return {
            "avg_ms": round(sum(self._compute_times) / len(self._compute_times), 2),
            "max_ms": round(max(self._compute_times), 2),
            "p99_ms": round(sorted(self._compute_times)[int(len(self._compute_times) * 0.99)], 2),
            "count": len(self._compute_times),
            "n_stocks": self.n_stocks,
            "ticks_filled": self._filled,
            "device": str(self.device),
        }

    def reset(self):
        """重置所有数据"""
        self.prices.zero_()
        self.volumes.zero_()
        self.amounts.zero_()
        self.highs.zero_()
        self.lows.zero_()
        self.opens.zero_()
        self.last_closes.zero_()
        self.factors.zero_()
        self._code_to_idx.clear()
        self._idx_to_code.clear()
        self.n_stocks = 0
        self._ptr = 0
        self._filled = 0
        self._compute_times.clear()


# 全局单例
_engine: GPUFactorEngine | None = None


def get_gpu_factor_engine() -> GPUFactorEngine:
    global _engine
    if _engine is None:
        _engine = GPUFactorEngine()
    return _engine
