"""国内期货数据源 - 基于 AKShare"""

from typing import Literal
import polars as pl
import akshare as ak
from datetime import datetime, timedelta
from loguru import logger


class FuturesFeed:
    """国内期货数据源 (AKShare)"""

    name = "futures_akshare"

    # 主力合约代码映射
    MAIN_CONTRACTS = {
        "IF": "沪深300股指期货",
        "IC": "中证500股指期货",
        "IH": "上证50股指期货",
        "IM": "中证1000股指期货",
        "TF": "5年期国债期货",
        "T": "10年期国债期货",
        "TS": "2年期国债期货",
        "AU": "黄金期货",
        "AG": "白银期货",
        "CU": "铜期货",
        "AL": "铝期货",
        "ZN": "锌期货",
        "NI": "镍期货",
        "SN": "锡期货",
        "RB": "螺纹钢期货",
        "HC": "热轧卷板期货",
        "I": "铁矿石期货",
        "J": "焦炭期货",
        "JM": "焦煤期货",
        "ZC": "动力煤期货",
        "MA": "甲醇期货",
        "TA": "PTA期货",
        "PVC": "PVC期货",
        "PP": "聚丙烯期货",
        "PE": "聚乙烯期货",
        "M": "豆粕期货",
        "Y": "豆油期货",
        "P": "棕榈油期货",
        "C": "玉米期货",
        "CS": "玉米淀粉期货",
        "SR": "白糖期货",
        "CF": "棉花期货",
        "OI": "菜籽油期货",
        "RM": "菜粕期货",
    }

    def is_available(self) -> bool:
        """检查数据源是否可用"""
        try:
            # 测试获取一个合约
            df = ak.futures_zh_minute_sina(symbol="IF0", period="1")
            return df is not None and not df.empty
        except Exception:
            return False

    def get_main_contract(self, symbol: str) -> str:
        """获取主力合约代码

        Args:
            symbol: 品种代码 (如 "IF", "AU")

        Returns:
            主力合约代码 (如 "IF2506")
        """
        try:
            # 获取主力合约
            df = ak.futures_zh_realtime(symbol=symbol)
            if df is not None and not df.empty:
                # 成交量最大的即为主力
                main = df.loc[df['volume'].idxmax()]
                return main['symbol']
        except Exception as e:
            logger.error(f"获取主力合约失败 {symbol}: {e}")

        # 默认返回当前月份+1的主力合约
        now = datetime.now()
        # 期货主力通常是当前月之后的第2个季月合约
        months = [3, 6, 9, 12]
        year = now.year % 100
        month = now.month

        for m in months:
            if m > month:
                return f"{symbol}{year}{m:02d}"
        # 如果当前月已过所有季月，取明年3月
        return f"{symbol}{(year+1):02d}03"

    def get_quotes(self, codes: list[str]) -> list[dict]:
        """获取实时行情

        Args:
            codes: 合约代码列表 (如 ["IF0", "AU0"] 或具体合约 ["IF2506"])

        Returns:
            行情数据列表
        """
        results = []

        for code in codes:
            try:
                # 去掉尾部的0表示主力合约
                symbol = code.replace("0", "")
                df = ak.futures_zh_realtime(symbol=symbol)

                if df is not None and not df.empty:
                    # 如果是主力合约请求，取成交量最大的
                    if code.endswith("0"):
                        row = df.loc[df['volume'].idxmax()]
                    else:
                        # 查找具体合约
                        row = df[df['symbol'] == code].iloc[0] if not df[df['symbol'] == code].empty else df.iloc[0]

                    results.append({
                        "code": row['symbol'],
                        "name": self.MAIN_CONTRACTS.get(symbol, symbol),
                        "last_price": float(row['last_price']) if pd.notna(row['last_price']) else 0.0,
                        "bid_price": float(row['bid_price']) if pd.notna(row['bid_price']) else 0.0,
                        "ask_price": float(row['ask_price']) if pd.notna(row['ask_price']) else 0.0,
                        "bid_volume": int(row['bid_volume']) if pd.notna(row['bid_volume']) else 0,
                        "ask_volume": int(row['ask_volume']) if pd.notna(row['ask_volume']) else 0,
                        "volume": int(row['volume']) if pd.notna(row['volume']) else 0,
                        "open_interest": int(row['open_interest']) if pd.notna(row['open_interest']) else 0,
                        "high": float(row['high']) if pd.notna(row['high']) else 0.0,
                        "low": float(row['low']) if pd.notna(row['low']) else 0.0,
                        "open": float(row['open']) if pd.notna(row['open']) else 0.0,
                        "prev_close": float(row['prev_close']) if pd.notna(row['prev_close']) else 0.0,
                        "time": row['time'] if 'time' in row else datetime.now().strftime("%H:%M:%S"),
                    })

            except Exception as e:
                logger.error(f"获取期货行情失败 {code}: {e}")

        return results

    def get_kline(
        self,
        code: str,
        klt: int = 101,
        count: int = 500,
        start_date: str = None,
        end_date: str = None
    ) -> pl.DataFrame:
        """获取K线数据

        Args:
            code: 合约代码 (如 "IF0", "IF2506")
            klt: 周期 (5=5分钟, 15=15分钟, 30=30分钟, 60=60分钟, 101=日线)
            count: 获取条数
            start_date: 开始日期 "20250301"
            end_date: 结束日期 "20250328"

        Returns:
            Polars DataFrame
        """
        try:
            # 周期映射
            period_map = {
                5: "5",
                15: "15",
                30: "30",
                60: "60",
                101: "1"  # 日线用日频接口
            }

            if klt == 101:
                # 日线数据
                return self._get_daily_kline(code, start_date, end_date, count)
            else:
                # 分钟数据
                period = period_map.get(klt, "1")
                return self._get_minute_kline(code, period, count)

        except Exception as e:
            logger.error(f"获取期货K线失败 {code}: {e}")
            return pl.DataFrame()

    def _get_minute_kline(self, code: str, period: str = "1", count: int = 500) -> pl.DataFrame:
        """获取分钟K线"""
        try:
            # AKShare 期货分钟数据接口
            df = ak.futures_zh_minute_sina(symbol=code, period=period)

            if df is not None and not df.empty:
                # 转换为 polars
                data = pl.from_pandas(df)

                # 确保列名符合规范
                column_mapping = {
                    "datetime": "datetime",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume",
                }

                # 重命名列
                data = data.rename({k: v for k, v in column_mapping.items() if k in data.columns})

                # 添加合约代码
                data = data.with_columns([
                    pl.lit(code).alias("code"),
                    pl.lit(8).alias("market"),  # 8=期货
                ])

                # 转换时间格式
                if "datetime" in data.columns:
                    data = data.with_columns([
                        pl.col("datetime").str.to_datetime("%Y-%m-%d %H:%M:%S").alias("datetime")
                    ])

                # 取最近 count 条
                if len(data) > count:
                    data = data.tail(count)

                return data

        except Exception as e:
            logger.error(f"获取分钟K线失败 {code}: {e}")

        return pl.DataFrame()

    def _get_daily_kline(
        self,
        code: str,
        start_date: str = None,
        end_date: str = None,
        count: int = 500
    ) -> pl.DataFrame:
        """获取日线数据 - 使用AKShare期货历史行情接口"""
        try:
            # 去掉尾部0，获取品种代码
            symbol = code.replace("0", "")

            # 使用AKShare获取期货连续合约日线数据
            df = ak.futures_zh_daily_sina(symbol=f"{symbol}0")

            if df is not None and not df.empty:
                # 确保列名小写
                df.columns = df.columns.str.lower()

                # 转换为polars
                data = pl.from_pandas(df)

                # 添加合约信息
                data = data.with_columns([
                    pl.lit(code).alias("code"),
                    pl.lit(8).alias("market"),
                ])

                # 日期过滤
                if start_date:
                    start = datetime.strptime(start_date, "%Y%m%d").strftime("%Y-%m-%d")
                    data = data.filter(pl.col("date") >= start)
                if end_date:
                    end = datetime.strptime(end_date, "%Y%m%d").strftime("%Y-%m-%d")
                    data = data.filter(pl.col("date") <= end)

                # 取最近count条
                if len(data) > count:
                    data = data.tail(count)

                return data

        except Exception as e:
            logger.error(f"获取日线失败 {code}: {e}")

        return pl.DataFrame()

    def get_futures_list(self) -> pl.DataFrame:
        """获取所有期货品种列表"""
        try:
            df = ak.futures_zh_realtime(symbol="IF")
            if df is not None:
                return pl.from_pandas(df)
        except Exception as e:
            logger.error(f"获取期货列表失败: {e}")
        return pl.DataFrame()

    def get_futures_contracts(self, symbol: str) -> pl.DataFrame:
        """获取某品种所有可交易合约"""
        try:
            df = ak.futures_zh_realtime(symbol=symbol)
            if df is not None and not df.empty:
                return pl.from_pandas(df)
        except Exception as e:
            logger.error(f"获取合约列表失败 {symbol}: {e}")
        return pl.DataFrame()


class FuturesStore:
    """期货数据存储扩展"""

    def __init__(self, store):
        """
        Args:
            store: DuckDBStore 实例
        """
        self.store = store
        self._init_futures_tables()

    def _init_futures_tables(self):
        """初始化期货数据表"""
        self.store.conn.execute("""
            CREATE TABLE IF NOT EXISTS futures_contracts (
                code VARCHAR,
                name VARCHAR,
                market INTEGER DEFAULT 8,
                symbol VARCHAR,
                exchange VARCHAR,
                contract_month VARCHAR,
                list_date DATE,
                expire_date DATE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (code, market)
            )
        """)

        self.store.conn.execute("""
            CREATE TABLE IF NOT EXISTS futures_kline_daily (
                code VARCHAR,
                market INTEGER DEFAULT 8,
                date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                open_interest BIGINT,
                settlement DOUBLE,
                PRIMARY KEY (code, market, date)
            )
        """)

        self.store.conn.execute("""
            CREATE TABLE IF NOT EXISTS futures_kline_minute (
                code VARCHAR,
                market INTEGER DEFAULT 8,
                datetime TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                open_interest BIGINT,
                PRIMARY KEY (code, market, datetime)
            )
        """)

        logger.info("Futures tables initialized")

    def save_futures_daily(self, code: str, data: pl.DataFrame):
        """保存期货日线数据"""
        if data.is_empty():
            return

        # 确保列存在
        required = ["date", "open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in data.columns]
        if missing:
            logger.warning(f"缺少列: {missing}")
            return

        df = data.with_columns([
            pl.lit(code).alias("code"),
            pl.lit(8).alias("market"),
        ])

        self.store.conn.execute("""
            INSERT OR REPLACE INTO futures_kline_daily
            SELECT code, market, date::DATE, open, high, low, close,
                   volume::BIGINT,
                   NULL as open_interest,
                   NULL as settlement
            FROM df
        """)
        logger.info(f"Saved {len(data)} daily records for {code}")

    def save_futures_minute(self, code: str, data: pl.DataFrame):
        """保存期货分钟数据"""
        if data.is_empty():
            return

        required = ["datetime", "open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in data.columns]
        if missing:
            logger.warning(f"缺少列: {missing}")
            return

        df = data.with_columns([
            pl.lit(code).alias("code"),
            pl.lit(8).alias("market"),
        ])

        self.store.conn.execute("""
            INSERT OR REPLACE INTO futures_kline_minute
            SELECT code, market, datetime::TIMESTAMP, open, high, low, close,
                   volume::BIGINT,
                   NULL as open_interest
            FROM df
        """)
        logger.info(f"Saved {len(data)} minute records for {code}")

    def get_futures_daily(
        self,
        code: str,
        start_date: str = "",
        end_date: str = ""
    ) -> pl.DataFrame:
        """查询期货日线"""
        query = "SELECT * FROM futures_kline_daily WHERE code = ? AND market = 8"
        params = [code]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date"

        return self.store.conn.execute(query, params).pl()

    def get_futures_minute(
        self,
        code: str,
        start: str = "",
        end: str = ""
    ) -> pl.DataFrame:
        """查询期货分钟线"""
        query = "SELECT * FROM futures_kline_minute WHERE code = ? AND market = 8"
        params = [code]

        if start:
            query += " AND datetime >= ?"
            params.append(start)
        if end:
            query += " AND datetime <= ?"
            params.append(end)
        query += " ORDER BY datetime"

        return self.store.conn.execute(query, params).pl()


# 全局实例
_futures_feed: FuturesFeed | None = None
_futures_store: FuturesStore | None = None


def get_futures_feed() -> FuturesFeed:
    """获取期货数据源实例"""
    global _futures_feed
    if _futures_feed is None:
        _futures_feed = FuturesFeed()
    return _futures_feed


def get_futures_store(store=None) -> FuturesStore:
    """获取期货存储实例"""
    global _futures_store
    if _futures_store is None:
        if store is None:
            from .store import get_store
            store = get_store()
        _futures_store = FuturesStore(store)
    return _futures_store


# 便捷函数
def fetch_and_save_futures_data(
    code: str,
    period: str = "daily",  # daily, 1min, 5min, 15min, 30min, 60min
    start_date: str = None,
    end_date: str = None,
    count: int = 500
):
    """获取并保存期货数据到本地数据库

    Args:
        code: 合约代码 (如 "IF0", "AU2506")
        period: 数据周期
        start_date: 开始日期 "20250301"
        end_date: 结束日期 "20250328"
        count: 获取条数
    """
    feed = get_futures_feed()
    store = get_futures_store()

    # 周期映射
    klt_map = {
        "daily": 101,
        "1min": 1,
        "5min": 5,
        "15min": 15,
        "30min": 30,
        "60min": 60,
    }
    klt = klt_map.get(period, 101)

    # 获取数据
    logger.info(f"获取 {code} {period} 数据...")
    data = feed.get_kline(code, klt=klt, count=count, start_date=start_date, end_date=end_date)

    if data.is_empty():
        logger.warning(f"未获取到数据: {code}")
        return

    # 保存数据
    if period == "daily":
        store.save_futures_daily(code, data)
    else:
        store.save_futures_minute(code, data)

    logger.info(f"已保存 {len(data)} 条记录")


def load_futures_for_backtest(
    code: str,
    period: str = "daily",
    start_date: str = None,
    end_date: str = None
) -> pl.DataFrame:
    """加载期货数据用于回测

    Returns:
        DataFrame 包含 date/datetime, open, high, low, close, volume
    """
    store = get_futures_store()

    if period == "daily":
        return store.get_futures_daily(code, start_date, end_date)
    else:
        return store.get_futures_minute(code, start_date, end_date)
