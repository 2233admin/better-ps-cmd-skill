"""Legacy DuckDB cache and local analysis store.

This module remains in service for macro helpers, compatibility APIs, and
manual tooling. It is not the canonical A-share research/control-plane fact
source. A-share promotion runs must use the PIT parquet lake instead.
"""

from pathlib import Path

import duckdb
import polars as pl
from loguru import logger

from .paths import resolve_duckdb_path

DB_PATH = resolve_duckdb_path()


class DuckDBStore:
    """DuckDB 存储层"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self._init_tables()

    def _init_tables(self):
        """初始化数据表"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS kline_daily (
                code VARCHAR,
                market INTEGER,
                date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                amount DOUBLE,
                PRIMARY KEY (code, market, date)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS kline_minute (
                code VARCHAR,
                market INTEGER,
                datetime TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                amount DOUBLE,
                PRIMARY KEY (code, market, datetime)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tick_data (
                code VARCHAR,
                market INTEGER,
                datetime TIMESTAMP,
                price DOUBLE,
                volume INTEGER,
                buyorsell INTEGER,
                PRIMARY KEY (code, market, datetime)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bond_info (
                code VARCHAR PRIMARY KEY,
                name VARCHAR,
                market INTEGER,
                stock_code VARCHAR,
                stock_name VARCHAR,
                conversion_price DOUBLE,
                premium_rate DOUBLE,
                ytm DOUBLE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY,
                code VARCHAR,
                market INTEGER,
                direction VARCHAR,
                price DOUBLE,
                volume INTEGER,
                amount DOUBLE,
                strategy VARCHAR,
                status VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                filled_at TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS trades_id_seq START 1
        """)

        # === 宏观模块表 ===
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_indicators (
                code VARCHAR,
                category VARCHAR,
                name VARCHAR,
                date VARCHAR,
                value DOUBLE,
                unit VARCHAR DEFAULT '%',
                PRIMARY KEY (code, date)
            )
        """)

        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS briefings_id_seq START 1
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_briefings (
                id INTEGER PRIMARY KEY DEFAULT(nextval('briefings_id_seq')),
                content TEXT,
                generated_at VARCHAR,
                indicator_count INTEGER,
                model VARCHAR,
                focus VARCHAR DEFAULT 'all'
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_knowledge_log (
                doc_id VARCHAR PRIMARY KEY,
                source VARCHAR,
                category VARCHAR,
                text_length INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_allocation (
                date VARCHAR,
                asset_class VARCHAR,
                target_pct DOUBLE,
                actual_pct DOUBLE,
                value DOUBLE DEFAULT 0,
                notes VARCHAR DEFAULT '',
                PRIMARY KEY (date, asset_class)
            )
        """)

        logger.info("DuckDB tables initialized")

    def save_kline_daily(self, code: str, market: int, data: list[dict]):
        """保存日线数据"""
        if not data:
            return
        df = pl.DataFrame(data)
        df = df.with_columns([
            pl.lit(code).alias("code"),
            pl.lit(market).alias("market"),
        ])
        self.conn.execute("""
            INSERT OR REPLACE INTO kline_daily
            SELECT code, market, date::DATE, open, high, low, close, volume::BIGINT, amount
            FROM df
        """)
        logger.debug(f"Saved {len(data)} daily records for {code}")

    def save_kline_minute(self, code: str, market: int, data: list[dict]):
        """保存分钟线数据"""
        if not data:
            return
        df = pl.DataFrame(data)
        df = df.with_columns([
            pl.lit(code).alias("code"),
            pl.lit(market).alias("market"),
        ])
        self.conn.execute("""
            INSERT OR REPLACE INTO kline_minute
            SELECT code, market, datetime::TIMESTAMP, open, high, low, close, volume::BIGINT, amount
            FROM df
        """)
        logger.debug(f"Saved {len(data)} minute records for {code}")

    def get_kline_daily(
        self, code: str, market: int | None = None, start_date: str = "", end_date: str = ""
    ) -> pl.DataFrame:
        """查询日线数据"""
        if market is not None:
            query = "SELECT * FROM kline_daily WHERE code = ? AND market = ?"
            params: list = [code, market]
        else:
            query = "SELECT * FROM kline_daily WHERE code = ?"
            params = [code]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date"

        result = self.conn.execute(query, params).pl()
        return result

    def get_kline_minute(
        self, code: str, market: int, start: str = "", end: str = ""
    ) -> pl.DataFrame:
        """查询分钟线数据"""
        query = "SELECT * FROM kline_minute WHERE code = ? AND market = ?"
        params = [code, market]
        if start:
            query += " AND datetime >= ?"
            params.append(start)
        if end:
            query += " AND datetime <= ?"
            params.append(end)
        query += " ORDER BY datetime"

        return self.conn.execute(query, params).pl()

    def save_bond_info(self, bonds: list[dict]):
        """保存可转债信息"""
        if not bonds:
            return
        df = pl.DataFrame(bonds)
        self.conn.execute("""
            INSERT OR REPLACE INTO bond_info (code, name, market, stock_code, stock_name,
                                               conversion_price, premium_rate, ytm)
            SELECT code, name, market, stock_code, stock_name,
                   conversion_price, premium_rate, ytm
            FROM df
        """)

    def record_trade(
        self,
        code: str,
        market: int,
        direction: str,
        price: float,
        volume: int,
        amount: float,
        strategy: str = "manual",
        status: str = "filled",
    ) -> int:
        """记录交易"""
        result = self.conn.execute("""
            INSERT INTO trades (id, code, market, direction, price, volume, amount, strategy, status)
            VALUES (nextval('trades_id_seq'), ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, [code, market, direction, price, volume, amount, strategy, status])
        trade_id = result.fetchone()[0]
        logger.info(f"Trade #{trade_id}: {direction} {code} @ {price} x {volume}")
        return trade_id

    def get_trades(self, limit: int = 100) -> pl.DataFrame:
        """查询交易记录"""
        return self.conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", [limit]
        ).pl()

    def execute_query(self, query: str, params: list | None = None) -> pl.DataFrame:
        """执行自定义查询"""
        if params:
            return self.conn.execute(query, params).pl()
        return self.conn.execute(query).pl()

    def close(self):
        """关闭连接"""
        self.conn.close()


# 全局单例
_store: DuckDBStore | None = None


def get_store() -> DuckDBStore:
    global _store
    if _store is None:
        _store = DuckDBStore()
    return _store
