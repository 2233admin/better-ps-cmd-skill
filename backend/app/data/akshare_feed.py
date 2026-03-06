"""AKShare 数据源 - 补充可转债指标、北向资金等数据"""

import asyncio
from functools import lru_cache

import akshare as ak
import polars as pl
from loguru import logger


class AKShareFeed:
    """AKShare 数据源封装"""

    @staticmethod
    def get_bond_list() -> pl.DataFrame:
        """获取可转债列表（含转股溢价率等指标）"""
        try:
            df = ak.bond_cb_jsl()
            if df is not None and not df.empty:
                result = pl.from_pandas(df)
                logger.info(f"AKShare: got {len(result)} convertible bonds")
                return result
        except Exception as e:
            logger.error(f"AKShare bond_cb_jsl error: {e}")
        return pl.DataFrame()

    @staticmethod
    def get_bond_realtime() -> pl.DataFrame:
        """获取可转债实时行情"""
        try:
            df = ak.bond_cb_index_jsl()
            if df is not None and not df.empty:
                return pl.from_pandas(df)
        except Exception as e:
            logger.error(f"AKShare bond realtime error: {e}")
        return pl.DataFrame()

    @staticmethod
    def get_north_flow() -> pl.DataFrame:
        """获取北向资金实时流向"""
        try:
            df = ak.stock_hsgt_north_net_flow_in_em()
            if df is not None and not df.empty:
                return pl.from_pandas(df)
        except Exception as e:
            logger.error(f"AKShare north flow error: {e}")
        return pl.DataFrame()

    @staticmethod
    def get_sector_flow() -> pl.DataFrame:
        """获取板块资金流"""
        try:
            df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
            if df is not None and not df.empty:
                return pl.from_pandas(df)
        except Exception as e:
            logger.error(f"AKShare sector flow error: {e}")
        return pl.DataFrame()

    @staticmethod
    def get_stock_history(code: str, period: str = "daily", adjust: str = "qfq") -> pl.DataFrame:
        """获取个股历史行情

        Args:
            code: 股票代码 (如 "600000")
            period: daily/weekly/monthly
            adjust: qfq(前复权)/hfq(后复权)/""(不复权)
        """
        try:
            df = ak.stock_zh_a_hist(symbol=code, period=period, adjust=adjust)
            if df is not None and not df.empty:
                return pl.from_pandas(df)
        except Exception as e:
            logger.error(f"AKShare stock history error: {e}")
        return pl.DataFrame()

    @staticmethod
    def get_bond_history(code: str) -> pl.DataFrame:
        """获取可转债历史行情"""
        try:
            df = ak.bond_zh_hs_cov_daily(symbol=code)
            if df is not None and not df.empty:
                return pl.from_pandas(df)
        except Exception as e:
            logger.error(f"AKShare bond history error: {e}")
        return pl.DataFrame()

    @staticmethod
    def get_dragon_tiger() -> pl.DataFrame:
        """获取龙虎榜数据"""
        try:
            df = ak.stock_lhb_detail_em()
            if df is not None and not df.empty:
                return pl.from_pandas(df)
        except Exception as e:
            logger.error(f"AKShare dragon tiger error: {e}")
        return pl.DataFrame()


# 全局实例
akshare_feed = AKShareFeed()
