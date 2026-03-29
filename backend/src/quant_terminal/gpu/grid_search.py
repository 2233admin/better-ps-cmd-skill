"""
GPU网格搜索模块

真GPU并行的参数优化
"""

from typing import Dict, List, Tuple, Optional, Iterable
from itertools import product
import time
import torch
import polars as pl
import numpy as np
from loguru import logger

from .core import GPUCore


class GPUGridSearch:
    """
    GPU网格搜索器

    使用PyTorch CUDA进行真并行参数搜索
    支持超大规模参数空间 (10,000+ 组合)

    Example:
        >>> searcher = GPUGridSearch()
        >>> results = searcher.search(df, param_grid)
        >>> best = max(results, key=lambda x: x['sharpe_ratio'])
    """

    def __init__(self, gpu_core: Optional[GPUCore] = None):
        """
        初始化GPU网格搜索

        Args:
            gpu_core: GPUCore实例，如未提供则自动创建
        """
        self.gpu = gpu_core or GPUCore()
        self.device = self.gpu.device
        self.stream = self.gpu.stream

        if not self.gpu.is_available:
            logger.warning("[GPUGridSearch] CUDA不可用，回退到CPU模式")

    def search(
        self,
        df: pl.DataFrame,
        param_grid: Dict[str, Iterable],
        strategy_func: callable,
        metric: str = 'sharpe_ratio'
    ) -> List[Dict]:
        """
        执行网格搜索

        Args:
            df: OHLCV数据
            param_grid: 参数字典 {param_name: [values]}
            strategy_func: 策略函数 (data, **params) -> signals
            metric: 排序指标

        Returns:
            结果列表
        """
        # 生成所有参数组合
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        param_combinations = list(product(*param_values))

        logger.info(f"[GPUGridSearch] 参数组合数: {len(param_combinations)}")

        # 执行批量评估
        return self._batch_evaluate(df, param_combinations, param_names, strategy_func)

    def _batch_evaluate(
        self,
        df: pl.DataFrame,
        param_combinations: List[Tuple],
        param_names: List[str],
        strategy_func: callable
    ) -> List[Dict]:
        """
        批量评估所有参数组合

        Args:
            df: 数据
            param_combinations: 参数组合列表
            param_names: 参数名列表
            strategy_func: 策略函数

        Returns:
            结果列表
        """
        n_params = len(param_combinations)

        # 预加载数据到GPU
        data_gpu = self._load_data_to_gpu(df)
        n = len(data_gpu['close'])

        results = []
        start_time = time.time()

        # 按n_periods分组优化
        grouped_params = self._group_by_n_periods(param_combinations, param_names)

        for period, params_list in grouped_params.items():
            if len(params_list) == 0:
                continue

            # 预计算该周期的rolling统计
            hh, ll, hc, lc = self.gpu.rolling_max(data_gpu['high'], period), \
                            self.gpu.rolling_min(data_gpu['low'], period), \
                            self.gpu.rolling_max(data_gpu['close'], period), \
                            self.gpu.rolling_min(data_gpu['close'], period)
            range_val = torch.maximum(hh - lc, hc - ll)

            # 批量处理该周期内的所有k1,k2组合
            k1s = torch.tensor([p['k1'] for p in params_list], device=self.device)
            k2s = torch.tensor([p['k2'] for p in params_list], device=self.device)

            # 并行计算所有上轨下轨
            upper = data_gpu['open'].unsqueeze(0) + k1s.unsqueeze(1) * range_val.unsqueeze(0)
            lower = data_gpu['open'].unsqueeze(0) - k2s.unsqueeze(1) * range_val.unsqueeze(0)

            # 向量化信号生成
            signals = self._vectorized_signals_batch(
                data_gpu['high'], data_gpu['low'], upper, lower
            )

            # 批量计算指标
            metrics = self._batch_calculate_metrics(data_gpu['close'], signals)

            # 组装结果
            for i, params_dict in enumerate(params_list):
                results.append({
                    **params_dict,
                    'sharpe_ratio': metrics['sharpe'][i].item(),
                    'total_return': metrics['returns'][i].item(),
                    'max_drawdown': metrics['drawdowns'][i].item(),
                    'trades': metrics['trades'][i].item()
                })

        elapsed = time.time() - start_time
        logger.info(f"[GPUGridSearch] 评估完成: {n_params}组合, 耗时{elapsed:.2f}秒, "
                   f"速度: {n_params/elapsed:.0f}组合/秒")

        return results

    def _load_data_to_gpu(self, df: pl.DataFrame) -> Dict[str, torch.Tensor]:
        """加载数据到GPU显存"""
        return {
            'high': torch.tensor(df['high'].to_numpy(), dtype=torch.float32, device=self.device),
            'low': torch.tensor(df['low'].to_numpy(), dtype=torch.float32, device=self.device),
            'close': torch.tensor(df['close'].to_numpy(), dtype=torch.float32, device=self.device),
            'open': torch.tensor(df['open'].to_numpy(), dtype=torch.float32, device=self.device),
        }

    def _group_by_n_periods(
        self,
        param_combinations: List[Tuple],
        param_names: List[str]
    ) -> Dict[int, List[Dict]]:
        """按n_periods分组参数"""
        grouped = {}

        for combo in param_combinations:
            params_dict = dict(zip(param_names, combo))
            period = params_dict.get('n_periods', 5)

            if period not in grouped:
                grouped[period] = []
            grouped[period].append(params_dict)

        return grouped

    def _vectorized_signals_batch(
        self,
        highs: torch.Tensor,
        lows: torch.Tensor,
        upper: torch.Tensor,
        lower: torch.Tensor
    ) -> torch.Tensor:
        """
        向量化批量信号生成

        Args:
            highs: 最高价 [n]
            lows: 最低价 [n]
            upper: 上轨 [m, n] (m个参数组合)
            lower: 下轨 [m, n]

        Returns:
            信号 [m, n]
        """
        n_params, n = upper.shape

        # 扩展highs/lows匹配batch维度
        highs_batch = highs.unsqueeze(0).expand(n_params, -1)
        lows_batch = lows.unsqueeze(0).expand(n_params, -1)

        # 原始信号
        long_signals = (highs_batch >= upper).to(torch.int8)
        short_signals = -(lows_batch <= lower).to(torch.int8)
        raw_signals = torch.clamp(long_signals + short_signals, -1, 1)

        # 应用状态机
        signals = torch.zeros_like(raw_signals)

        for i in range(n):
            if i == 0:
                signals[:, i] = raw_signals[:, i]
            else:
                prev_pos = signals[:, i-1]
                curr_raw = raw_signals[:, i]

                # 状态转换
                new_signal = torch.where(
                    prev_pos == 0,
                    curr_raw,
                    torch.where(
                        (prev_pos == 1) & (curr_raw == -1),
                        torch.full((n_params,), -1, device=self.device, dtype=torch.int8),
                        torch.where(
                            (prev_pos == -1) & (curr_raw == 1),
                            torch.full((n_params,), 1, device=self.device, dtype=torch.int8),
                            torch.zeros(n_params, device=self.device, dtype=torch.int8)
                        )
                    )
                )
                signals[:, i] = new_signal

        return signals

    def _batch_calculate_metrics(
        self,
        closes: torch.Tensor,
        signals: torch.Tensor
    ) -> Dict[str, torch.Tensor]:
        """
        批量计算回测指标

        Args:
            closes: 收盘价 [n]
            signals: 信号 [m, n]

        Returns:
            指标字典
        """
        n_params, n = signals.shape

        # 计算收益率
        price_changes = (closes[1:] - closes[:-1]) / closes[:-1]
        price_changes_batch = price_changes.unsqueeze(0).expand(n_params, -1)

        trade_signals = signals[:, :-1].to(torch.float32)
        returns = price_changes_batch * trade_signals

        # 扣除成本
        costs = (trade_signals != 0).to(torch.float32) * 0.0003
        returns = returns - costs

        # 总收益
        total_returns = returns.sum(dim=1)

        # 交易次数
        trades = (trade_signals != 0).sum(dim=1)

        # 夏普比率
        means = returns.sum(dim=1) / (trades + 1e-10)
        stds = torch.zeros(n_params, device=self.device)

        for i in range(n_params):
            valid_rets = returns[i][trade_signals[i] != 0]
            if len(valid_rets) > 1:
                stds[i] = valid_rets.std()

        sharpes = means / (stds + 1e-10) * np.sqrt(252)

        # 最大回撤
        cumsum = torch.cumsum(returns, dim=1)
        running_max = torch.cummax(cumsum, dim=1)[0]
        drawdowns = cumsum - running_max
        max_dd = drawdowns.min(dim=1)[0]

        return {
            'returns': total_returns,
            'trades': trades,
            'sharpe': sharpes,
            'drawdowns': max_dd.abs()
        }

    def get_best_params(
        self,
        results: List[Dict],
        metric: str = 'sharpe_ratio',
        top_n: int = 1
    ) -> List[Dict]:
        """
        获取最佳参数

        Args:
            results: 搜索结果
            metric: 排序指标
            top_n: 返回前几名

        Returns:
            最佳参数列表
        """
        sorted_results = sorted(
            results,
            key=lambda x: x.get(metric, -999),
            reverse=True
        )
        return sorted_results[:top_n]

    def print_results(
        self,
        results: List[Dict],
        top_n: int = 10
    ):
        """打印搜索结果"""
        sorted_results = sorted(
            results,
            key=lambda x: x.get('sharpe_ratio', -999),
            reverse=True
        )

        print(f"\n{'排名':<6} {'n_periods':<12} {'k1':<8} {'k2':<8} {'夏普':<10} {'收益':<10} {'回撤':<8}")
        print("-" * 70)

        for i, r in enumerate(sorted_results[:top_n], 1):
            print(f"{i:<6} {r.get('n_periods', ''):<12} "
                  f"{r.get('k1', 0):<8.2f} {r.get('k2', 0):<8.2f} "
                  f"{r.get('sharpe_ratio', 0):<10.2f} "
                  f"{r.get('total_return', 0):>+8.2%} "
                  f"{r.get('max_drawdown', 0):>6.2%}")
