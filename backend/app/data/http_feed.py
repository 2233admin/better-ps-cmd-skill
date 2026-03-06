"""HTTP 数据源 - 基于 adata 库 + 新浪后备

adata: 多源聚合(新浪/腾讯/东财/百度)，覆盖股票、可转债、资金流、龙虎榜
新浪: 轻量后备，批量实时行情
"""

import re
from datetime import datetime, timedelta

import httpx
from loguru import logger


def _sina_prefix(code: str) -> str:
    if code.startswith(("6", "11", "5")):
        return f"sh{code}"
    return f"sz{code}"


class AdataFeed:
    """adata 数据源 — 股票K线 + 可转债实时行情 + 资金流"""

    name = "adata"

    def __init__(self):
        self._adata = None

    def _ensure_import(self):
        if self._adata is None:
            import adata
            self._adata = adata

    def is_available(self) -> bool:
        try:
            self._ensure_import()
            return True
        except ImportError:
            return False

    def get_quotes(self, codes: list[str]) -> list[dict]:
        """实时行情 — 可转债走 adata.bond，股票走新浪"""
        self._ensure_import()
        results = []

        # 分离可转债和股票代码
        bond_codes = [c for c in codes if c.startswith(("11", "12", "13"))]
        stock_codes = [c for c in codes if c not in bond_codes]

        # 可转债: adata 一次拿全市场，再过滤
        if bond_codes:
            try:
                df = self._adata.bond.market.list_market_current()
                if not df.empty:
                    bond_set = set(bond_codes)
                    for _, row in df.iterrows():
                        if str(row.get("bond_code", "")) in bond_set:
                            results.append({
                                "code": str(row["bond_code"]),
                                "name": str(row.get("bond_name", "")),
                                "price": float(row.get("price", 0)),
                                "open": float(row.get("open", 0)),
                                "high": float(row.get("high", 0)),
                                "low": float(row.get("low", 0)),
                                "last_close": float(row.get("pre_close", 0)),
                                "vol": float(row.get("volume", 0)),
                                "amount": float(row.get("amount", 0)),
                                "change_pct": float(row.get("change_pct", 0)),
                                "market": 1 if str(row["bond_code"]).startswith("11") else 0,
                            })
            except Exception as e:
                logger.error(f"adata bond quotes error: {e}")

        # 股票: adata 的 list_market_current 不太好用，回退到新浪
        if stock_codes:
            results.extend(SinaFeed().get_quotes(stock_codes))

        return results

    def get_kline(self, code: str, klt: int = 101, count: int = 500) -> list[dict]:
        """K线数据 — adata 支持日/周/月线"""
        self._ensure_import()
        # klt -> adata k_type: 101=日(1), 102=周(2), 103=月(3)
        k_type_map = {101: 1, 102: 2, 103: 3}
        k_type = k_type_map.get(klt)
        if k_type is None:
            # 分钟线 adata 不支持，回退
            return []

        start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y-%m-%d")
        try:
            df = self._adata.stock.market.get_market(
                stock_code=code, k_type=k_type, start_date=start_date
            )
            if df is None or df.empty:
                return []
            records = []
            for _, row in df.tail(count).iterrows():
                records.append({
                    "datetime": str(row.get("trade_date", "")),
                    "open": float(row.get("open", 0)),
                    "close": float(row.get("close", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "vol": float(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)),
                    "change_pct": float(row.get("change_pct", 0)),
                })
            return records
        except Exception as e:
            logger.error(f"adata kline {code} error: {e}")
            return []

    def get_bond_list(self) -> list[dict]:
        """获取全市场可转债列表"""
        self._ensure_import()
        try:
            df = self._adata.bond.info.all_bond_code()
            if df is None or df.empty:
                return []
            return df.to_dict("records")
        except Exception as e:
            logger.error(f"adata bond list error: {e}")
            return []

    def get_capital_flow(self, code: str, days: int = 30) -> list[dict]:
        """个股资金流向"""
        self._ensure_import()
        try:
            df = self._adata.stock.market.get_capital_flow(stock_code=code)
            if df is None or df.empty:
                return []
            return df.tail(days).to_dict("records")
        except Exception as e:
            logger.error(f"adata capital flow {code} error: {e}")
            return []

    def get_north_flow(self) -> list[dict]:
        """北向资金流向"""
        self._ensure_import()
        try:
            df = self._adata.sentiment.north.north_flow_current()
            if df is None or df.empty:
                return []
            return df.to_dict("records")
        except Exception as e:
            logger.error(f"adata north flow error: {e}")
            return []


class SinaFeed:
    """新浪实时行情后备 — 批量查询快，可转债/股票都支持"""

    name = "sina"

    def __init__(self):
        self._client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=10,
            follow_redirects=True,
        )

    def is_available(self) -> bool:
        return True

    def get_quotes(self, codes: list[str]) -> list[dict]:
        if not codes:
            return []
        sina_codes = ",".join(_sina_prefix(c) for c in codes)
        try:
            resp = self._client.get(f"https://hq.sinajs.cn/list={sina_codes}")
            text = resp.content.decode("gb2312", errors="replace")
            return self._parse(text, codes)
        except Exception as e:
            logger.error(f"Sina quotes error: {e}")
            return []

    @staticmethod
    def _parse(text: str, codes: list[str]) -> list[dict]:
        results = []
        lines = [ln for ln in text.strip().split("\n") if ln.strip()]
        for i, line in enumerate(lines):
            match = re.search(r'"(.+)"', line)
            if not match:
                continue
            parts = match.group(1).split(",")
            if len(parts) < 10:
                continue
            code = codes[i] if i < len(codes) else ""
            results.append({
                "code": code,
                "name": parts[0],
                "open": float(parts[1] or 0),
                "last_close": float(parts[2] or 0),
                "price": float(parts[3] or 0),
                "high": float(parts[4] or 0),
                "low": float(parts[5] or 0),
                "vol": float(parts[8] or 0),
                "amount": float(parts[9] or 0),
                "market": 1 if code.startswith(("6", "11")) else 0,
            })
        return results

    def get_kline(self, code: str, klt: int = 101, count: int = 500) -> list[dict]:
        """新浪K线，仅作最后后备"""
        prefix = _sina_prefix(code)
        scale_map = {5: 5, 15: 15, 30: 30, 60: 60, 101: 240, 102: 1200, 103: 7200}
        scale = scale_map.get(klt, 240)
        url = (
            f"https://quotes.sina.cn/cn/api/jsonp.php/var/"
            f"CN_MarketDataService.getKLineData"
            f"?symbol={prefix}&scale={scale}&ma=no&datalen={count}"
        )
        try:
            resp = self._client.get(url)
            text = resp.content.decode("gb2312", errors="replace")
            json_match = re.search(r"\[.+\]", text, re.DOTALL)
            if not json_match:
                return []
            import json
            data = json.loads(json_match.group(0))
            return [
                {
                    "datetime": item["day"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "vol": float(item["volume"]),
                }
                for item in data
            ]
        except Exception as e:
            logger.error(f"Sina kline {code} error: {e}")
            return []
