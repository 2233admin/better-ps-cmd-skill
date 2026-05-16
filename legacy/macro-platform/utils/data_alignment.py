"""
数据对齐工具
处理宏观数据（月度）和市场数据（日度）的时间对齐
"""
import pandas as pd
import numpy as np
from datetime import datetime


def align_macro_to_daily(macro_df: pd.DataFrame, daily_df: pd.DataFrame,
                         date_col: str = 'date', value_col: str = 'value') -> pd.DataFrame:
    """
    将月度宏观数据对齐到日度市场数据
    使用前向填充，避免未来函数

    Args:
        macro_df: 宏观数据 DataFrame (date, value)
        daily_df: 日度数据 DataFrame (date, ...)
        date_col: 日期列名
        value_col: 值列名

    Returns:
        对齐后的 DataFrame，包含日度宏观数据
    """
    # 确保日期格式
    macro_df = macro_df.copy()
    daily_df = daily_df.copy()

    macro_df[date_col] = pd.to_datetime(macro_df[date_col])
    daily_df[date_col] = pd.to_datetime(daily_df[date_col])

    # 设置日期为索引
    macro_df = macro_df.set_index(date_col).sort_index()
    daily_df = daily_df.set_index(date_col).sort_index()

    # 前向填充到日度
    # 使用 reindex + ffill 确保只使用历史数据
    aligned = macro_df.reindex(daily_df.index, method='ffill')

    return aligned.reset_index()


def align_daily_to_monthly(daily_df: pd.DataFrame, date_col: str = 'date',
                          agg_dict: dict = None) -> pd.DataFrame:
    """
    将日度数据聚合到月度

    Args:
        daily_df: 日度数据 DataFrame
        date_col: 日期列名
        agg_dict: 聚合规则，如 {'close': 'last', 'volume': 'sum'}

    Returns:
        月度聚合后的 DataFrame
    """
    df = daily_df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col)

    # 默认聚合规则
    if agg_dict is None:
        agg_dict = {
            'close': 'last',
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'volume': 'sum'
        }

    # 只聚合存在的列
    agg_dict = {k: v for k, v in agg_dict.items() if k in df.columns}

    monthly = df.resample('M').agg(agg_dict)

    return monthly.reset_index()


def merge_macro_market(macro_df: pd.DataFrame, market_df: pd.DataFrame,
                       macro_name: str = 'macro_value',
                       date_col: str = 'date') -> pd.DataFrame:
    """
    合并宏观数据和市场数据

    Args:
        macro_df: 宏观数据 (date, value)
        market_df: 市场数据 (date, close, ...)
        macro_name: 宏观指标列名
        date_col: 日期列名

    Returns:
        合并后的 DataFrame
    """
    # 对齐宏观数据到日度
    aligned_macro = align_macro_to_daily(macro_df, market_df, date_col)

    # 重命名宏观值列
    if 'value' in aligned_macro.columns:
        aligned_macro = aligned_macro.rename(columns={'value': macro_name})

    # 合并
    merged = market_df.merge(aligned_macro[[date_col, macro_name]],
                            on=date_col, how='left')

    return merged


def calculate_returns(df: pd.DataFrame, price_col: str = 'close',
                     periods: list = [1, 5, 20]) -> pd.DataFrame:
    """
    计算收益率

    Args:
        df: 数据 DataFrame
        price_col: 价格列名
        periods: 计算周期列表

    Returns:
        添加收益率列的 DataFrame
    """
    df = df.copy()

    for period in periods:
        col_name = f'return_{period}d'
        df[col_name] = df[price_col].pct_change(period) * 100

    return df


def validate_alignment(df: pd.DataFrame, date_col: str = 'date') -> dict:
    """
    验证数据对齐质量

    Args:
        df: 对齐后的 DataFrame
        date_col: 日期列名

    Returns:
        验证结果字典
    """
    results = {
        'total_rows': len(df),
        'date_range': (df[date_col].min(), df[date_col].max()),
        'missing_dates': df[date_col].isna().sum(),
        'duplicate_dates': df[date_col].duplicated().sum(),
    }

    # 检查数值列的缺失
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    for col in numeric_cols:
        results[f'{col}_missing'] = df[col].isna().sum()

    return results


if __name__ == "__main__":
    # 测试代码
    print("数据对齐工具模块")
    print("主要功能:")
    print("  - align_macro_to_daily: 月度 → 日度（前向填充）")
    print("  - align_daily_to_monthly: 日度 → 月度（聚合）")
    print("  - merge_macro_market: 合并宏观和市场数据")
    print("  - calculate_returns: 计算收益率")
    print("  - validate_alignment: 验证对齐质量")
