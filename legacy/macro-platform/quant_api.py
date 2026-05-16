"""
量化数据查询 API
提供简单易用的数据查询接口
"""
import duckdb
import pandas as pd
from typing import Optional, List
from datetime import datetime, timedelta


DB_PATH = "C:/Users/Administrator/quant-terminal/data/quant.duckdb"


class QuantDataAPI:
    """量化数据查询API"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _get_conn(self):
        """获取只读连接"""
        return duckdb.connect(self.db_path, read_only=True)

    # ========== 通达信数据 ==========

    def get_stock_daily(self, symbol: str, start_date: str = None,
                       end_date: str = None, limit: int = None) -> pd.DataFrame:
        """
        获取股票日线数据

        Args:
            symbol: 股票代码（如 'sh600000'）
            start_date: 开始日期 'YYYY-MM-DD'
            end_date: 结束日期 'YYYY-MM-DD'
            limit: 限制返回条数

        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount
        """
        conn = self._get_conn()

        where_clauses = [f"symbol = '{symbol}'"]
        if start_date:
            where_clauses.append(f"date >= '{start_date}'")
        if end_date:
            where_clauses.append(f"date <= '{end_date}'")

        where_str = " AND ".join(where_clauses)
        limit_str = f"LIMIT {limit}" if limit else ""

        query = f"""
            SELECT date, open, high, low, close, volume, amount
            FROM tdx_daily
            WHERE {where_str}
            ORDER BY date DESC
            {limit_str}
        """

        df = conn.execute(query).df()
        conn.close()
        return df

    def get_latest_price(self, symbol: str) -> dict:
        """
        获取最新价格

        Returns:
            dict with keys: date, open, high, low, close, volume
        """
        df = self.get_stock_daily(symbol, limit=1)
        if df.empty:
            return None
        return df.iloc[0].to_dict()

    def get_market_overview(self, date: str = None) -> pd.DataFrame:
        """
        获取市场概况（某日所有股票）

        Args:
            date: 日期 'YYYY-MM-DD'，默认最新交易日

        Returns:
            DataFrame with all stocks on that date
        """
        conn = self._get_conn()

        if date is None:
            date_clause = "date = (SELECT MAX(date) FROM tdx_daily)"
        else:
            date_clause = f"date = '{date}'"

        query = f"""
            SELECT symbol, date, open, high, low, close, volume, amount,
                   (close - open) / open * 100 as change_pct
            FROM tdx_daily
            WHERE {date_clause}
            ORDER BY symbol
        """

        df = conn.execute(query).df()
        conn.close()
        return df

    def get_top_gainers(self, date: str = None, limit: int = 10) -> pd.DataFrame:
        """
        获取涨幅榜

        Args:
            date: 日期，默认最新交易日
            limit: 返回数量

        Returns:
            DataFrame sorted by change_pct DESC
        """
        df = self.get_market_overview(date)
        df = df.sort_values('change_pct', ascending=False).head(limit)
        return df

    def get_stock_list(self, market: str = None) -> List[str]:
        """
        获取股票列表

        Args:
            market: 'sh' 或 'sz'，默认全部

        Returns:
            List of stock symbols
        """
        conn = self._get_conn()

        where_clause = ""
        if market:
            where_clause = f"WHERE symbol LIKE '{market}%'"

        query = f"""
            SELECT DISTINCT symbol
            FROM tdx_daily
            {where_clause}
            ORDER BY symbol
        """

        stocks = conn.execute(query).fetchall()
        conn.close()
        return [s[0] for s in stocks]

    # ========== 宏观数据 ==========

    def get_macro_indicator(self, indicator_code: str,
                           start_date: str = None,
                           end_date: str = None) -> pd.DataFrame:
        """
        获取宏观指标数据

        Args:
            indicator_code: 指标代码（如 'A01010101' for CPI）
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame with columns: date, value, indicator_name
        """
        conn = self._get_conn()

        where_clauses = [f"indicator_code = '{indicator_code}'"]
        if start_date:
            where_clauses.append(f"date >= '{start_date}'")
        if end_date:
            where_clauses.append(f"date <= '{end_date}'")

        where_str = " AND ".join(where_clauses)

        query = f"""
            SELECT date, value, indicator_name
            FROM macro_indicators
            WHERE {where_str}
            ORDER BY date DESC
        """

        df = conn.execute(query).df()
        conn.close()
        return df

    def get_cpi(self, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取CPI数据"""
        return self.get_macro_indicator('A01010101', start_date, end_date)

    def get_ppi(self, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取PPI数据"""
        return self.get_macro_indicator('A01010201', start_date, end_date)

    # ========== 财务数据 ==========

    def get_financial_balance(self, symbol: str, limit: int = 10) -> pd.DataFrame:
        """
        获取资产负债表

        Args:
            symbol: 股票代码（不含市场前缀，如 '600000'）
            limit: 返回期数

        Returns:
            DataFrame with balance sheet data
        """
        conn = self._get_conn()

        query = f"""
            SELECT *
            FROM financial_balance_raw_1
            WHERE symbol = '{symbol}'
            ORDER BY report_date DESC
            LIMIT {limit}
        """

        df = conn.execute(query).df()
        conn.close()
        return df

    def get_financial_income(self, symbol: str, limit: int = 10) -> pd.DataFrame:
        """获取利润表"""
        conn = self._get_conn()

        query = f"""
            SELECT *
            FROM financial_income_raw_1
            WHERE symbol = '{symbol}'
            ORDER BY report_date DESC
            LIMIT {limit}
        """

        df = conn.execute(query).df()
        conn.close()
        return df

    # ========== 技术指标计算 ==========

    def calculate_ma(self, symbol: str, period: int = 20,
                    start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        计算移动平均线

        Args:
            symbol: 股票代码
            period: 周期
            start_date, end_date: 日期范围

        Returns:
            DataFrame with columns: date, close, ma
        """
        df = self.get_stock_daily(symbol, start_date, end_date)
        df = df.sort_values('date')
        df[f'ma{period}'] = df['close'].rolling(window=period).mean()
        return df[['date', 'close', f'ma{period}']]

    def calculate_returns(self, symbol: str, period: int = 1,
                         start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        计算收益率

        Args:
            symbol: 股票代码
            period: 周期（1=日收益率）
            start_date, end_date: 日期范围

        Returns:
            DataFrame with columns: date, close, returns
        """
        df = self.get_stock_daily(symbol, start_date, end_date)
        df = df.sort_values('date')
        df['returns'] = df['close'].pct_change(periods=period) * 100
        return df[['date', 'close', 'returns']]

    # ========== 实用工具 ==========

    def get_trading_dates(self, start_date: str, end_date: str) -> List[str]:
        """
        获取交易日列表

        Returns:
            List of trading dates
        """
        conn = self._get_conn()

        query = f"""
            SELECT DISTINCT date
            FROM tdx_daily
            WHERE date >= '{start_date}' AND date <= '{end_date}'
            ORDER BY date
        """

        dates = conn.execute(query).fetchall()
        conn.close()
        return [str(d[0]) for d in dates]

    def get_latest_trading_date(self) -> str:
        """获取最新交易日"""
        conn = self._get_conn()
        date = conn.execute("SELECT MAX(date) FROM tdx_daily").fetchone()[0]
        conn.close()
        return str(date)


# ========== 使用示例 ==========

if __name__ == "__main__":
    api = QuantDataAPI()

    print("=" * 60)
    print("量化数据 API 使用示例")
    print("=" * 60)

    # 1. 获取股票日线数据
    print("\n1. 获取上证指数最近10天数据:")
    df = api.get_stock_daily('sh000001', limit=10)
    print(df)

    # 2. 获取最新价格
    print("\n2. 获取最新价格:")
    price = api.get_latest_price('sh000001')
    print(price)

    # 3. 获取涨幅榜
    print("\n3. 今日涨幅前5:")
    df = api.get_top_gainers(limit=5)
    print(df[['symbol', 'close', 'change_pct']])

    # 4. 获取CPI数据
    print("\n4. 最近CPI数据:")
    df = api.get_cpi()
    print(df.head())

    # 5. 计算移动平均线
    print("\n5. 计算20日均线:")
    df = api.calculate_ma('sh000001', period=20, limit=30)
    print(df.tail())

    # 6. 获取最新交易日
    print("\n6. 最新交易日:")
    print(api.get_latest_trading_date())

    print("\n" + "=" * 60)
    print("API 测试完成！")
    print("=" * 60)
