"""宏观指标采集 - 通过 akshare 统一采集"""

from datetime import datetime

import akshare as ak
import polars as pl
from loguru import logger

from .config import INDICATOR_MAP, INDICATOR_REGISTRY, IndicatorDef
from ..data.store import get_store


def fetch_indicator(indicator: IndicatorDef) -> list[dict]:
    """采集单个指标数据"""
    try:
        func = getattr(ak, indicator.akshare_func, None)
        if func is None:
            logger.warning(f"akshare function not found: {indicator.akshare_func}")
            return []

        df = func(**indicator.akshare_params)
        if df is None or df.empty:
            return []

        records = []
        for _, row in df.tail(24).iterrows():  # 最近24期
            date_val = str(row.get(indicator.date_column, ""))
            value = row.get(indicator.value_column)
            if value is not None and date_val:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    continue
                records.append({
                    "code": indicator.code,
                    "category": indicator.category,
                    "name": indicator.name,
                    "date": date_val,
                    "value": value,
                    "unit": indicator.unit,
                })

        logger.info(f"Fetched {len(records)} records for {indicator.name}")
        return records

    except Exception as e:
        logger.error(f"Error fetching {indicator.name}: {e}")
        return []


def fetch_all_indicators() -> dict[str, list[dict]]:
    """采集所有注册指标"""
    results = {}
    for ind in INDICATOR_REGISTRY:
        records = fetch_indicator(ind)
        if records:
            results[ind.code] = records
            _save_to_duckdb(records)
    return results


def fetch_by_codes(codes: list[str]) -> dict[str, list[dict]]:
    """按指标代码采集"""
    results = {}
    for code in codes:
        ind = INDICATOR_MAP.get(code)
        if ind:
            records = fetch_indicator(ind)
            if records:
                results[code] = records
                _save_to_duckdb(records)
    return results


def fetch_by_category(category: str) -> dict[str, list[dict]]:
    """按分类采集"""
    codes = [ind.code for ind in INDICATOR_REGISTRY if ind.category == category]
    return fetch_by_codes(codes)


def _save_to_duckdb(records: list[dict]):
    """保存指标数据到 DuckDB"""
    if not records:
        return
    store = get_store()
    df = pl.DataFrame(records)
    store.conn.execute("""
        INSERT OR REPLACE INTO macro_indicators (code, category, name, date, value, unit)
        SELECT code, category, name, date, value, unit FROM df
    """)


def get_latest_indicators(category: str | None = None) -> list[dict]:
    """获取各指标最新值"""
    store = get_store()
    query = """
        SELECT code, category, name, date, value, unit
        FROM macro_indicators
        WHERE (code, date) IN (
            SELECT code, MAX(date) FROM macro_indicators GROUP BY code
        )
    """
    if category:
        query = """
            SELECT code, category, name, date, value, unit
            FROM macro_indicators
            WHERE category = ? AND (code, date) IN (
                SELECT code, MAX(date) FROM macro_indicators WHERE category = ? GROUP BY code
            )
        """
        result = store.conn.execute(query, [category, category]).fetchall()
    else:
        result = store.conn.execute(query).fetchall()

    return [
        {"code": r[0], "category": r[1], "name": r[2], "date": r[3], "value": r[4], "unit": r[5]}
        for r in result
    ]


def get_indicator_history(code: str, limit: int = 24) -> list[dict]:
    """获取指标历史数据"""
    store = get_store()
    result = store.conn.execute(
        "SELECT date, value FROM macro_indicators WHERE code = ? ORDER BY date DESC LIMIT ?",
        [code, limit],
    ).fetchall()
    return [{"date": r[0], "value": r[1]} for r in result]
