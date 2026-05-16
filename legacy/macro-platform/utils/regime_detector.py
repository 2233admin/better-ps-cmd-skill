"""
宏观 Regime 识别工具
基于规则引擎识别宏观经济状态
"""
import pandas as pd
import numpy as np
from typing import Dict, List


class RegimeDetector:
    """宏观 Regime 识别器"""

    def __init__(self):
        self.regimes = {
            'expansion': '扩张',
            'contraction': '收缩',
            'stagflation': '滞胀',
            'recovery': '复苏'
        }

    def detect_regime(self, macro_data: Dict[str, float]) -> str:
        """
        基于规则识别当前 Regime

        Args:
            macro_data: 宏观指标字典，如 {'PMI': 52.0, 'CPI': 2.5, 'PPI': 1.0, 'M2': 8.5}

        Returns:
            Regime 名称
        """
        pmi = macro_data.get('PMI', 50)
        cpi = macro_data.get('CPI', 2)
        ppi = macro_data.get('PPI', 0)
        m2 = macro_data.get('M2', 8)

        # 规则引擎
        # 扩张：PMI > 50, CPI 温和, PPI 上行
        if pmi > 50 and 1 < cpi < 3 and ppi > 0:
            return 'expansion'

        # 滞胀：PMI < 50, CPI 高, PPI 高
        if pmi < 50 and cpi > 3 and ppi > 2:
            return 'stagflation'

        # 收缩：PMI < 50, CPI 下行, PPI 下行
        if pmi < 50 and cpi < 2 and ppi < 0:
            return 'contraction'

        # 复苏：PMI 上行, CPI 低, M2 宽松
        if pmi > 48 and cpi < 2 and m2 > 8:
            return 'recovery'

        # 默认：根据 PMI 判断
        return 'expansion' if pmi >= 50 else 'contraction'

    def detect_regime_series(self, df: pd.DataFrame,
                            indicator_cols: Dict[str, str]) -> pd.Series:
        """
        对时间序列数据识别 Regime

        Args:
            df: 数据 DataFrame
            indicator_cols: 指标列名映射，如 {'PMI': 'pmi_col', 'CPI': 'cpi_col'}

        Returns:
            Regime 序列
        """
        regimes = []

        for idx, row in df.iterrows():
            macro_data = {}
            for indicator, col_name in indicator_cols.items():
                if col_name in df.columns:
                    macro_data[indicator] = row[col_name]

            regime = self.detect_regime(macro_data)
            regimes.append(regime)

        return pd.Series(regimes, index=df.index)

    def get_regime_description(self, regime: str) -> Dict:
        """
        获取 Regime 的详细描述

        Args:
            regime: Regime 名称

        Returns:
            描述字典
        """
        descriptions = {
            'expansion': {
                'name': '扩张',
                'characteristics': 'PMI > 50, CPI 温和, PPI 上行',
                'market_implication': '股市通常表现良好，周期股领涨',
                'policy_stance': '货币政策中性偏紧',
                'color': 'green'
            },
            'contraction': {
                'name': '收缩',
                'characteristics': 'PMI < 50, CPI 下行, PPI 下行',
                'market_implication': '股市承压，防御性板块相对抗跌',
                'policy_stance': '货币政策宽松',
                'color': 'red'
            },
            'stagflation': {
                'name': '滞胀',
                'characteristics': 'PMI < 50, CPI 高, PPI 高',
                'market_implication': '股市表现较差，商品表现较好',
                'policy_stance': '货币政策两难',
                'color': 'orange'
            },
            'recovery': {
                'name': '复苏',
                'characteristics': 'PMI 上行, CPI 低, M2 宽松',
                'market_implication': '股市开始反弹，成长股表现较好',
                'policy_stance': '货币政策宽松',
                'color': 'blue'
            }
        }

        return descriptions.get(regime, {
            'name': '未知',
            'characteristics': '无法识别',
            'market_implication': '无',
            'policy_stance': '无',
            'color': 'gray'
        })

    def find_similar_periods(self, current_regime: str, df: pd.DataFrame,
                            regime_col: str = 'regime',
                            date_col: str = 'date',
                            lookback_days: int = 90) -> pd.DataFrame:
        """
        查找历史上相似的 Regime 时期

        Args:
            current_regime: 当前 Regime
            df: 历史数据 DataFrame
            regime_col: Regime 列名
            date_col: 日期列名
            lookback_days: 向后看的天数

        Returns:
            相似时期的 DataFrame
        """
        # 找到所有相同 Regime 的时期
        similar_mask = df[regime_col] == current_regime

        # 找到 Regime 切换点
        regime_changes = df[regime_col] != df[regime_col].shift(1)
        regime_starts = df[regime_changes & similar_mask].index

        similar_periods = []

        for start_idx in regime_starts:
            # 获取该时期后 lookback_days 的数据
            end_idx = min(start_idx + lookback_days, len(df))
            period_data = df.iloc[start_idx:end_idx]

            if len(period_data) >= lookback_days * 0.8:  # 至少80%的数据
                similar_periods.append({
                    'start_date': period_data[date_col].iloc[0],
                    'end_date': period_data[date_col].iloc[-1],
                    'duration_days': len(period_data),
                    'data': period_data
                })

        return pd.DataFrame(similar_periods)

    def regime_transition_matrix(self, regime_series: pd.Series) -> pd.DataFrame:
        """
        计算 Regime 转移矩阵

        Args:
            regime_series: Regime 时间序列

        Returns:
            转移矩阵 DataFrame
        """
        regimes = regime_series.unique()
        matrix = pd.DataFrame(0, index=regimes, columns=regimes)

        for i in range(len(regime_series) - 1):
            current = regime_series.iloc[i]
            next_regime = regime_series.iloc[i + 1]
            matrix.loc[current, next_regime] += 1

        # 转换为概率
        matrix = matrix.div(matrix.sum(axis=1), axis=0)

        return matrix

    def regime_statistics(self, df: pd.DataFrame, regime_col: str = 'regime',
                         return_col: str = 'return') -> pd.DataFrame:
        """
        计算各 Regime 下的市场表现统计

        Args:
            df: 数据 DataFrame
            regime_col: Regime 列名
            return_col: 收益率列名

        Returns:
            统计结果 DataFrame
        """
        stats = []

        for regime in df[regime_col].unique():
            subset = df[df[regime_col] == regime]

            if return_col in subset.columns:
                stats.append({
                    'regime': regime,
                    'regime_name': self.regimes.get(regime, regime),
                    'count': len(subset),
                    'mean_return': subset[return_col].mean(),
                    'std_return': subset[return_col].std(),
                    'median_return': subset[return_col].median(),
                    'positive_days': (subset[return_col] > 0).sum(),
                    'negative_days': (subset[return_col] < 0).sum(),
                    'win_rate': (subset[return_col] > 0).sum() / len(subset) * 100
                })

        return pd.DataFrame(stats)


if __name__ == "__main__":
    print("Regime 识别工具模块")
    print("主要功能:")
    print("  - detect_regime: 识别当前 Regime")
    print("  - detect_regime_series: 时间序列 Regime 识别")
    print("  - get_regime_description: 获取 Regime 描述")
    print("  - find_similar_periods: 查找历史相似时期")
    print("  - regime_transition_matrix: Regime 转移矩阵")
    print("  - regime_statistics: Regime 统计分析")

    # 测试
    detector = RegimeDetector()
    test_data = {'PMI': 52.0, 'CPI': 2.5, 'PPI': 1.0, 'M2': 8.5}
    regime = detector.detect_regime(test_data)
    print(f"\n测试数据: {test_data}")
    print(f"识别结果: {regime} ({detector.regimes[regime]})")
