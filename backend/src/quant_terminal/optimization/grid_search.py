"""
网格搜索优化器

CPU版本的参数网格搜索
"""

from typing import Dict, List, Any, Iterable
from itertools import product
import time
import polars as pl
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp


class GridSearchOptimizer:
    """
    网格搜索优化器

    穷举所有参数组合，找到最优参数

    Example:
        >>> optimizer = GridSearchOptimizer(n_jobs=4)
        >>> results = optimizer.optimize(df, param_grid, strategy_func)
        >>> best = optimizer.get_best(results)
    """

    def __init__(self, n_jobs: int = -1):
        """
        初始化优化器

        Args:
            n_jobs: 并行进程数，-1表示使用所有CPU核心
        """
        self.n_jobs = n_jobs if n_jobs > 0 else mp.cpu_count()

    def optimize(
        self,
        df: pl.DataFrame,
        param_grid: Dict[str, Iterable],
        strategy_func: callable,
        metric: str = 'sharpe_ratio'
    ) -> List[Dict]:
        """
        执行网格搜索优化

        Args:
            df: OHLCV数据
            param_grid: 参数字典 {param_name: [values]}
            strategy_func: 策略函数 (data, **params) -> signals
            metric: 排序指标

        Returns:
            结果列表
        """
        # 生成参数组合
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(product(*param_values))

        print(f"[GridSearch] 总参数组合: {len(combinations)}")
        print(f"[GridSearch] 使用 {self.n_jobs} 个进程")

        # 并行评估
        start_time = time.time()

        if self.n_jobs == 1:
            results = [
                self._evaluate_single(df, combo, param_names, strategy_func)
                for combo in combinations
            ]
        else:
            results = self._parallel_evaluate(
                df, combinations, param_names, strategy_func
            )

        elapsed = time.time() - start_time
        print(f"[GridSearch] 完成! 耗时: {elapsed:.2f}秒, 速度: {len(combinations)/elapsed:.1f}组合/秒")

        return [r for r in results if r is not None]

    def _evaluate_single(
        self,
        df: pl.DataFrame,
        params: tuple,
        param_names: List[str],
        strategy_func: callable
    ) -> Dict:
        """评估单个参数组合"""
        try:
            param_dict = dict(zip(param_names, params))
            signals = strategy_func(df, **param_dict)
            metrics = self._calculate_metrics(df, signals)
            return {**param_dict, **metrics}
        except Exception as e:
            print(f"[GridSearch] 评估失败 {params}: {e}")
            return None

    def _parallel_evaluate(
        self,
        df: pl.DataFrame,
        combinations: List[tuple],
        param_names: List[str],
        strategy_func: callable
    ) -> List[Dict]:
        """并行评估参数组合"""
        results = []

        with ProcessPoolExecutor(max_workers=self.n_jobs) as executor:
            futures = {
                executor.submit(
                    self._evaluate_single,
                    df,
                    combo,
                    param_names,
                    strategy_func
                ): combo
                for combo in combinations
            }

            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)

        return results

    def _calculate_metrics(
        self,
        df: pl.DataFrame,
        signals: pl.DataFrame
    ) -> Dict[str, float]:
        """计算回测指标"""
        # 合并信号
        merged = df.join(signals, on='datetime', how='left')
        merged = merged.with_columns([
            pl.col('signal').fill_null(0)
        ])

        # 计算收益
        merged = merged.with_columns([
            ((pl.col('close') - pl.col('close').shift(1)) / pl.col('close').shift(1)).alias('returns')
        ])

        merged = merged.with_columns([
            (pl.col('returns') * pl.col('signal').shift(1)).alias('strategy_returns')
        ])

        # 过滤有效交易
        valid_returns = merged.filter(pl.col('signal').shift(1) != 0)

        if len(valid_returns) < 2:
            return {
                'sharpe_ratio': -999,
                'total_return': 0,
                'max_drawdown': 0,
                'trades': 0
            }

        rets = valid_returns['strategy_returns'].to_numpy()

        # 计算指标
        total_return = float(rets.sum())
        sharpe = float(rets.mean() / (rets.std() + 1e-10) * (252 ** 0.5))

        # 最大回撤
        cumsum = rets.cumsum()
        running_max = cumsum.cummax()
        drawdown = cumsum - running_max
        max_dd = abs(float(drawdown.min()))

        return {
            'sharpe_ratio': sharpe,
            'total_return': total_return,
            'max_drawdown': max_dd,
            'trades': len(valid_returns)
        }

    def get_best(
        self,
        results: List[Dict],
        metric: str = 'sharpe_ratio'
    ) -> Dict:
        """获取最佳参数"""
        return max(results, key=lambda x: x.get(metric, -999))


class RandomSearchOptimizer(GridSearchOptimizer):
    """
    随机搜索优化器

    在参数空间随机采样，适合大规模参数空间
    """

    def __init__(self, n_jobs: int = -1, n_iter: int = 100):
        super().__init__(n_jobs)
        self.n_iter = n_iter

    def optimize(
        self,
        df: pl.DataFrame,
        param_distributions: Dict[str, Any],
        strategy_func: callable,
        metric: str = 'sharpe_ratio'
    ) -> List[Dict]:
        """
        执行随机搜索

        Args:
            df: 数据
            param_distributions: 参数分布 {param_name: distribution}
            strategy_func: 策略函数
            metric: 排序指标

        Returns:
            结果列表
        """
        import random

        # 随机采样参数组合
        combinations = []
        for _ in range(self.n_iter):
            params = {}
            for name, dist in param_distributions.items():
                if isinstance(dist, list):
                    params[name] = random.choice(dist)
                elif isinstance(dist, tuple) and len(dist) == 2:
                    # 假设为 (min, max) 均匀分布
                    params[name] = random.uniform(dist[0], dist[1])
                elif isinstance(dist, tuple) and len(dist) == 3:
                    # (min, max, step)
                    steps = int((dist[1] - dist[0]) / dist[2]) + 1
                    params[name] = dist[0] + random.randint(0, steps) * dist[2]
            combinations.append(tuple(params.values()))

        param_names = list(param_distributions.keys())

        print(f"[RandomSearch] 采样数: {self.n_iter}")

        # 复用父类的并行评估
        start_time = time.time()

        if self.n_jobs == 1:
            results = [
                self._evaluate_single(df, combo, param_names, strategy_func)
                for combo in combinations
            ]
        else:
            results = self._parallel_evaluate(
                df, combinations, param_names, strategy_func
            )

        elapsed = time.time() - start_time
        print(f"[RandomSearch] 完成! 耗时: {elapsed:.2f}秒")

        return [r for r in results if r is not None]
