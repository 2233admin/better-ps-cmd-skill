"""
宏观-市场关联分析核心引擎
整合数据对齐、相关性分析、Regime 识别
"""
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import sys
sys.path.append('C:/Users/Administrator/quant-terminal')

from utils.data_alignment import (
    align_macro_to_daily, merge_macro_market, calculate_returns
)
from utils.correlation_analysis import (
    rolling_correlation, lead_lag_correlation, correlation_matrix
)
from utils.regime_detector import RegimeDetector


DB_PATH = "C:/Users/Administrator/quant-terminal/data/quant.duckdb"


class MacroMarketAnalyzer:
    """宏观-市场关联分析器"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.regime_detector = RegimeDetector()

    def get_connection(self):
        """获取数据库连接"""
        return duckdb.connect(self.db_path, read_only=True)

    def load_macro_indicator(self, indicator_name: str,
                            start_date: str = None,
                            end_date: str = None) -> pd.DataFrame:
        """
        加载宏观指标数据

        Args:
            indicator_name: 指标名称（如 'CPI', 'PPI', 'PMI'）
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame with columns: date, value, indicator
        """
        conn = self.get_connection()

        where_clauses = [f"indicator = '{indicator_name}'"]
        if start_date:
            where_clauses.append(f"date >= '{start_date}'")
        if end_date:
            where_clauses.append(f"date <= '{end_date}'")

        where_str = " AND ".join(where_clauses)

        query = f"""
            SELECT date, value, indicator
            FROM macro_indicators
            WHERE {where_str}
            ORDER BY date
        """

        df = conn.execute(query).df()
        conn.close()

        return df

    def load_market_index(self, symbol: str = 'sh000001',
                         start_date: str = None,
                         end_date: str = None) -> pd.DataFrame:
        """
        加载市场指数数据

        Args:
            symbol: 指数代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame with columns: date, open, high, low, close, volume
        """
        conn = self.get_connection()

        where_clauses = [f"symbol = '{symbol}'"]
        if start_date:
            where_clauses.append(f"date >= '{start_date}'")
        if end_date:
            where_clauses.append(f"date <= '{end_date}'")

        where_str = " AND ".join(where_clauses)

        query = f"""
            SELECT date, open, high, low, close, volume
            FROM tdx_daily
            WHERE {where_str}
            ORDER BY date
        """

        df = conn.execute(query).df()
        conn.close()

        return df

    def analyze_macro_market_correlation(self, macro_indicator: str,
                                        market_symbol: str = 'sh000001',
                                        start_date: str = None,
                                        end_date: str = None,
                                        window: int = 60) -> Dict:
        """
        分析宏观指标与市场指数的相关性

        Args:
            macro_indicator: 宏观指标名称
            market_symbol: 市场指数代码
            start_date: 开始日期
            end_date: 结束日期
            window: 滚动窗口大小

        Returns:
            分析结果字典
        """
        # 加载数据
        macro_df = self.load_macro_indicator(macro_indicator, start_date, end_date)
        market_df = self.load_market_index(market_symbol, start_date, end_date)

        if macro_df.empty or market_df.empty:
            return {'error': '数据为空'}

        # 合并数据
        merged = merge_macro_market(macro_df, market_df, macro_indicator)

        # 计算收益率
        merged = calculate_returns(merged, 'close', [1, 5, 20])

        # 计算相关性
        corr_result = merged[['close', macro_indicator]].corr().iloc[0, 1]

        # 滚动相关性
        rolling_corr = rolling_correlation(
            merged['close'],
            merged[macro_indicator],
            window
        )

        # 领先滞后分析
        lead_lag = lead_lag_correlation(
            merged['close'].dropna(),
            merged[macro_indicator].dropna(),
            max_lag=12
        )

        return {
            'correlation': corr_result,
            'rolling_correlation': rolling_corr,
            'lead_lag': lead_lag,
            'merged_data': merged,
            'macro_indicator': macro_indicator,
            'market_symbol': market_symbol
        }

    def get_current_regime(self, date: str = None) -> Dict:
        """
        获取当前宏观 Regime

        Args:
            date: 日期，None 表示最新

        Returns:
            Regime 信息字典
        """
        # 加载最新的宏观指标
        indicators = ['CPI', 'PPI', 'PMI', 'M2']
        macro_data = {}

        for indicator in indicators:
            df = self.load_macro_indicator(indicator)
            if not df.empty:
                if date:
                    # 找到指定日期之前的最新值
                    df = df[df['date'] <= date]
                if not df.empty:
                    macro_data[indicator] = df['value'].iloc[-1]

        if not macro_data:
            return {'error': '无法获取宏观数据'}

        # 识别 Regime
        regime = self.regime_detector.detect_regime(macro_data)
        regime_desc = self.regime_detector.get_regime_description(regime)

        return {
            'regime': regime,
            'regime_name': regime_desc['name'],
            'description': regime_desc,
            'macro_data': macro_data,
            'date': date or datetime.now().strftime('%Y-%m-%d')
        }

    def analyze_regime_market_performance(self, market_symbol: str = 'sh000001',
                                         start_date: str = None,
                                         end_date: str = None) -> pd.DataFrame:
        """
        分析不同 Regime 下的市场表现

        Args:
            market_symbol: 市场指数代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            各 Regime 下的市场表现统计
        """
        # 加载市场数据
        market_df = self.load_market_index(market_symbol, start_date, end_date)

        # 加载宏观指标
        indicators = {}
        for ind in ['CPI', 'PPI', 'PMI', 'M2']:
            df = self.load_macro_indicator(ind, start_date, end_date)
            if not df.empty:
                aligned = align_macro_to_daily(df, market_df)
                indicators[ind] = aligned['value']

        # 合并所有指标
        for ind_name, ind_series in indicators.items():
            market_df[ind_name] = ind_series.values

        # 识别 Regime
        indicator_cols = {k: k for k in indicators.keys()}
        market_df['regime'] = self.regime_detector.detect_regime_series(
            market_df, indicator_cols
        )

        # 计算收益率
        market_df = calculate_returns(market_df, 'close', [1])

        # 统计各 Regime 表现
        stats = self.regime_detector.regime_statistics(
            market_df, 'regime', 'return_1d'
        )

        return stats

    def get_macro_dashboard_data(self) -> Dict:
        """
        获取宏观仪表板所需的所有数据

        Returns:
            仪表板数据字典
        """
        # 当前 Regime
        current_regime = self.get_current_regime()

        # 主要宏观指标最新值
        indicators = {}
        for ind in ['CPI', 'PPI', 'PMI', 'M2', 'GDP']:
            df = self.load_macro_indicator(ind)
            if not df.empty:
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest

                indicators[ind] = {
                    'value': latest['value'],
                    'prev_value': prev['value'],
                    'change': latest['value'] - prev['value'],
                    'change_pct': ((latest['value'] - prev['value']) / prev['value'] * 100)
                                  if prev['value'] != 0 else 0,
                    'date': latest['date']
                }

        # 主要市场指数最新值
        indices = {}
        for symbol, name in [('sh000001', '上证指数'), ('sh000300', '沪深300'),
                             ('sz399001', '深证成指'), ('sz399006', '创业板指')]:
            df = self.load_market_index(symbol)
            if not df.empty:
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest

                indices[name] = {
                    'symbol': symbol,
                    'close': latest['close'],
                    'change': latest['close'] - prev['close'],
                    'change_pct': ((latest['close'] - prev['close']) / prev['close'] * 100)
                                  if prev['close'] != 0 else 0,
                    'volume': latest['volume'],
                    'date': latest['date']
                }

        return {
            'regime': current_regime,
            'indicators': indicators,
            'indices': indices,
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }


if __name__ == "__main__":
    print("宏观-市场关联分析引擎")
    print("=" * 60)

    analyzer = MacroMarketAnalyzer()

    # 测试：获取仪表板数据
    print("\n获取仪表板数据...")
    dashboard = analyzer.get_macro_dashboard_data()

    print(f"\n当前 Regime: {dashboard['regime']['regime_name']}")
    print(f"更新时间: {dashboard['update_time']}")

    print("\n主要宏观指标:")
    for ind, data in dashboard['indicators'].items():
        print(f"  {ind}: {data['value']:.2f} ({data['change_pct']:+.2f}%)")

    print("\n主要市场指数:")
    for name, data in dashboard['indices'].items():
        print(f"  {name}: {data['close']:.2f} ({data['change_pct']:+.2f}%)")
