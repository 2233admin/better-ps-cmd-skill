"""
随机搜索优化器

使用随机采样的参数优化
"""

from typing import Dict, List, Any
import random
import numpy as np
import polars as pl
from .grid_search import GridSearchOptimizer


class RandomSearchOptimizer:
    """
    随机搜索优化器

    在连续参数空间中进行随机采样，适合参数空间过大的情况

    Example:
        >>> optimizer = RandomSearchOptimizer(n_iter=1000)
        >>> param_distributions = {
        ...     'n_periods': (3, 20),      # 均匀分布
        ...     'k1': (0.1, 1.0),          # 均匀分布
        ...     'k2': (0.1, 1.0),
        ... }
        >>> results = optimizer.optimize(df, param_distributions, strategy_func)
    """

    def __init__(self, n_iter: int = 100, random_state: int = 42):
        """
        初始化随机搜索优化器

        Args:
            n_iter: 迭代次数
            random_state: 随机种子
        """
        self.n_iter = n_iter
        self.random_state = random_state
        random.seed(random_state)
        np.random.seed(random_state)

    def optimize(
        self,
        df: pl.DataFrame,
        param_distributions: Dict[str, Any],
        strategy_func: callable,
        metric: str = 'sharpe_ratio'
    ) -> List[Dict]:
        """
        执行随机搜索优化

        Args:
            df: OHLCV数据
            param_distributions: 参数分布定义
                - 列表: 离散取值 [1, 2, 3, 4, 5]
                - 元组(min, max): 连续均匀分布
            strategy_func: 策略函数
            metric: 优化指标

        Returns:
            结果列表
        """
        results = []
        param_names = list(param_distributions.keys())

        for i in range(self.n_iter):
            # 采样参数
            params = self._sample_params(param_distributions)

            try:
                # 评估
                signals = strategy_func(df, **params)
                metrics = self._evaluate(df, signals)

                results.append({
                    **params,
                    **metrics
                })

                if (i + 1) % 100 == 0:
                    print(f"[RandomSearch] 已评估 {i + 1}/{self.n_iter}")

            except Exception as e:
                print(f"[RandomSearch] 评估失败 {params}: {e}")
                continue

        return results

    def _sample_params(self, distributions: Dict[str, Any]) -> Dict[str, Any]:
        """从分布中采样参数"""
        params = {}

        for name, dist in distributions.items():
            if isinstance(dist, list):
                # 离散选择
                params[name] = random.choice(dist)
            elif isinstance(dist, tuple):
                if len(dist) == 2:
                    # 连续均匀分布
                    params[name] = random.uniform(dist[0], dist[1])
                elif len(dist) == 3:
                    # 带步长的均匀分布
                    min_val, max_val, step = dist
                    steps = int((max_val - min_val) / step)
                    params[name] = min_val + random.randint(0, steps) * step
            elif isinstance(dist, dict):
                # 支持更复杂的分布
                dist_type = dist.get('type', 'uniform')
                if dist_type == 'uniform':
                    params[name] = random.uniform(dist['min'], dist['max'])
                elif dist_type == 'int':
                    params[name] = random.randint(dist['min'], dist['max'])
                elif dist_type == 'loguniform':
                    # 对数均匀分布
                    log_min = np.log(dist['min'])
                    log_max = np.log(dist['max'])
                    params[name] = np.exp(random.uniform(log_min, log_max))

        return params

    def _evaluate(
        self,
        df: pl.DataFrame,
        signals: pl.DataFrame
    ) -> Dict[str, float]:
        """评估信号表现"""
        from ..core.metrics import calculate_sharpe, calculate_drawdown

        if signals.is_empty():
            return {
                'sharpe_ratio': -999,
                'total_return': 0,
                'max_drawdown': 0,
                'trades': 0
            }

        # 计算收益
        merged = df.join(signals, on='datetime', how='left')
        merged = merged.with_columns([
            pl.col('signal').fill_null(0)
        ])

        merged = merged.with_columns([
            ((pl.col('close') - pl.col('close').shift(1)) / pl.col('close').shift(1)).alias('returns')
        ])

        merged = merged.with_columns([
            (pl.col('returns') * pl.col('signal').shift(1)).alias('strategy_returns')
        ])

        valid = merged.filter(pl.col('signal').shift(1) != 0)

        if len(valid) < 2:
            return {
                'sharpe_ratio': -999,
                'total_return': 0,
                'max_drawdown': 0,
                'trades': 0
            }

        rets = valid['strategy_returns'].to_numpy()

        return {
            'sharpe_ratio': calculate_sharpe(rets),
            'total_return': float(rets.sum()),
            'max_drawdown': calculate_drawdown(rets)['max_drawdown'],
            'trades': len(valid)
        }

    def get_best(
        self,
        results: List[Dict],
        metric: str = 'sharpe_ratio'
    ) -> Dict:
        """获取最佳结果"""
        return max(results, key=lambda x: x.get(metric, -999))
