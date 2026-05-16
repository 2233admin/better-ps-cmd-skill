"""
相关性分析工具
计算宏观指标与市场指数的相关性、领先滞后关系
"""
import pandas as pd
import numpy as np
from scipy import stats
from typing import Tuple, Dict


def rolling_correlation(series1: pd.Series, series2: pd.Series,
                       window: int = 60) -> pd.Series:
    """
    计算滚动相关系数

    Args:
        series1: 第一个时间序列
        series2: 第二个时间序列
        window: 滚动窗口大小（天）

    Returns:
        滚动相关系数序列
    """
    return series1.rolling(window).corr(series2)


def lead_lag_correlation(series1: pd.Series, series2: pd.Series,
                        max_lag: int = 12) -> pd.DataFrame:
    """
    计算领先滞后相关性

    Args:
        series1: 第一个时间序列（如市场指数）
        series2: 第二个时间序列（如宏观指标）
        max_lag: 最大滞后期数（月）

    Returns:
        DataFrame with columns: lag, correlation, p_value
    """
    results = []

    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            # series2 领先 series1
            s1 = series1.iloc[-lag:]
            s2 = series2.iloc[:lag]
        elif lag > 0:
            # series1 领先 series2
            s1 = series1.iloc[:-lag]
            s2 = series2.iloc[lag:]
        else:
            s1 = series1
            s2 = series2

        # 确保长度一致
        min_len = min(len(s1), len(s2))
        s1 = s1.iloc[:min_len]
        s2 = s2.iloc[:min_len]

        if len(s1) > 10:  # 至少需要10个数据点
            corr, p_value = stats.pearsonr(s1.dropna(), s2.dropna())
            results.append({
                'lag': lag,
                'correlation': corr,
                'p_value': p_value,
                'significant': p_value < 0.05
            })

    return pd.DataFrame(results)


def correlation_matrix(df: pd.DataFrame, columns: list = None) -> pd.DataFrame:
    """
    计算相关性矩阵

    Args:
        df: 数据 DataFrame
        columns: 要计算的列名列表，None 表示所有数值列

    Returns:
        相关性矩阵 DataFrame
    """
    if columns is None:
        columns = df.select_dtypes(include=[np.number]).columns.tolist()

    return df[columns].corr()


def granger_causality_test(series1: pd.Series, series2: pd.Series,
                           max_lag: int = 12) -> Dict:
    """
    格兰杰因果检验（简化版）

    Args:
        series1: 第一个时间序列
        series2: 第二个时间序列
        max_lag: 最大滞后期

    Returns:
        检验结果字典
    """
    try:
        from statsmodels.tsa.stattools import grangercausalitytests

        # 准备数据
        data = pd.DataFrame({
            'y': series1,
            'x': series2
        }).dropna()

        if len(data) < max_lag * 3:
            return {'error': '数据点不足'}

        # 执行检验
        results = grangercausalitytests(data[['y', 'x']], max_lag, verbose=False)

        # 提取 p 值
        p_values = {}
        for lag in range(1, max_lag + 1):
            if lag in results:
                # 使用 F 检验的 p 值
                p_value = results[lag][0]['ssr_ftest'][1]
                p_values[lag] = p_value

        # 找到最显著的滞后期
        if p_values:
            best_lag = min(p_values, key=p_values.get)
            best_p = p_values[best_lag]

            return {
                'best_lag': best_lag,
                'best_p_value': best_p,
                'significant': best_p < 0.05,
                'all_p_values': p_values,
                'interpretation': f"{'显著' if best_p < 0.05 else '不显著'}的格兰杰因果关系（滞后{best_lag}期）"
            }

    except ImportError:
        return {'error': '需要安装 statsmodels'}
    except Exception as e:
        return {'error': str(e)}

    return {'error': '未知错误'}


def regime_correlation(df: pd.DataFrame, regime_col: str,
                      col1: str, col2: str) -> pd.DataFrame:
    """
    按 Regime 分组计算相关性

    Args:
        df: 数据 DataFrame
        regime_col: Regime 列名
        col1: 第一个变量列名
        col2: 第二个变量列名

    Returns:
        各 Regime 下的相关性 DataFrame
    """
    results = []

    for regime in df[regime_col].unique():
        subset = df[df[regime_col] == regime]

        if len(subset) > 10:
            corr, p_value = stats.pearsonr(
                subset[col1].dropna(),
                subset[col2].dropna()
            )

            results.append({
                'regime': regime,
                'correlation': corr,
                'p_value': p_value,
                'n_samples': len(subset),
                'significant': p_value < 0.05
            })

    return pd.DataFrame(results)


def correlation_stability(series1: pd.Series, series2: pd.Series,
                         window: int = 60, step: int = 20) -> pd.DataFrame:
    """
    评估相关性的稳定性

    Args:
        series1: 第一个时间序列
        series2: 第二个时间序列
        window: 窗口大小
        step: 步长

    Returns:
        相关性稳定性统计 DataFrame
    """
    correlations = []
    dates = []

    for i in range(0, len(series1) - window, step):
        s1 = series1.iloc[i:i+window]
        s2 = series2.iloc[i:i+window]

        if len(s1.dropna()) > 10 and len(s2.dropna()) > 10:
            corr, _ = stats.pearsonr(s1.dropna(), s2.dropna())
            correlations.append(corr)
            dates.append(s1.index[-1] if hasattr(s1, 'index') else i+window)

    if correlations:
        return pd.DataFrame({
            'date': dates,
            'correlation': correlations,
            'mean': np.mean(correlations),
            'std': np.std(correlations),
            'min': np.min(correlations),
            'max': np.max(correlations)
        })

    return pd.DataFrame()


if __name__ == "__main__":
    print("相关性分析工具模块")
    print("主要功能:")
    print("  - rolling_correlation: 滚动相关系数")
    print("  - lead_lag_correlation: 领先滞后分析")
    print("  - correlation_matrix: 相关性矩阵")
    print("  - granger_causality_test: 格兰杰因果检验")
    print("  - regime_correlation: 按 Regime 分组相关性")
    print("  - correlation_stability: 相关性稳定性评估")
