"""
验证工具

数据验证和参数检查
"""

from typing import Dict, List, Any, Optional, Tuple
import polars as pl
import numpy as np


def validate_dataframe(
    df: pl.DataFrame,
    required_cols: List[str],
    check_empty: bool = True,
    check_nulls: bool = False
) -> Tuple[bool, str]:
    """
    验证DataFrame格式

    Args:
        df: 输入数据
        required_cols: 必需列
        check_empty: 检查是否为空
        check_nulls: 检查空值

    Returns:
        (是否有效, 错误信息)
    """
    # 检查空值
    if df is None:
        return False, "DataFrame为None"

    # 检查空DataFrame
    if check_empty and df.is_empty():
        return False, "DataFrame为空"

    # 检查必需列
    missing_cols = set(required_cols) - set(df.columns)
    if missing_cols:
        return False, f"缺少必需列: {missing_cols}"

    # 检查空值
    if check_nulls:
        for col in required_cols:
            null_count = df[col].is_null().sum()
            if null_count > 0:
                return False, f"列 {col} 有 {null_count} 个空值"

    return True, ""


def validate_ohlcv(df: pl.DataFrame) -> Tuple[bool, str]:
    """
    验证OHLCV数据

    Args:
        df: 输入数据

    Returns:
        (是否有效, 错误信息)
    """
    # 基础验证
    valid, msg = validate_dataframe(
        df,
        required_cols=["datetime", "open", "high", "low", "close", "volume"]
    )
    if not valid:
        return valid, msg

    # 检查OHLC关系
    invalid_count = (
        (df["high"] < df["low"]).sum() +
        (df["high"] < df["open"]).sum() +
        (df["high"] < df["close"]).sum() +
        (df["low"] > df["open"]).sum() +
        (df["low"] > df["close"]).sum()
    )

    if invalid_count > 0:
        return False, f"存在 {invalid_count} 条无效OHLC数据"

    # 检查价格非负
    if (df["close"] <= 0).any():
        return False, "存在非正价格数据"

    return True, ""


def validate_params(
    params: Dict[str, Any],
    schema: Dict[str, Dict[str, Any]]
) -> Tuple[bool, str]:
    """
    验证参数

    Args:
        params: 参数字典
        schema: 参数模式定义 {param_name: {type: ..., min: ..., max: ..., required: ...}}

    Returns:
        (是否有效, 错误信息)
    """
    for name, rules in schema.items():
        # 检查必需参数
        if rules.get("required", False) and name not in params:
            return False, f"缺少必需参数: {name}"

        if name not in params:
            continue

        value = params[name]

        # 检查类型
        if "type" in rules:
            if not isinstance(value, rules["type"]):
                return False, f"参数 {name} 类型错误，期望 {rules['type'].__name__}"

        # 检查范围
        if "min" in rules and value < rules["min"]:
            return False, f"参数 {name} 小于最小值 {rules['min']}"

        if "max" in rules and value > rules["max"]:
            return False, f"参数 {name} 大于最大值 {rules['max']}"

        # 检查可选值
        if "choices" in rules and value not in rules["choices"]:
            return False, f"参数 {name} 必须是 {rules['choices']} 之一"

    return True, ""


def validate_strategy_params(params: Dict[str, Any]) -> Tuple[bool, str]:
    """
    验证策略参数

    Args:
        params: 策略参数字典

    Returns:
        (是否有效, 错误信息)
    """
    schema = {
        "n_periods": {"type": int, "min": 1, "max": 100, "required": True},
        "k1": {"type": float, "min": 0.01, "max": 2.0, "required": True},
        "k2": {"type": float, "min": 0.01, "max": 2.0, "required": True},
    }

    return validate_params(params, schema)


def validate_backtest_config(config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    验证回测配置

    Args:
        config: 回测配置

    Returns:
        (是否有效, 错误信息)
    """
    schema = {
        "initial_capital": {"type": (int, float), "min": 1000, "required": True},
        "commission": {"type": float, "min": 0, "max": 0.1, "required": True},
        "slippage": {"type": float, "min": 0, "max": 0.1, "required": True},
    }

    return validate_params(config, schema)
