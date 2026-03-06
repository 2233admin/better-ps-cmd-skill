"""统一数据源管理器 - 按优先级自动切换数据源"""

import time
from typing import Protocol, runtime_checkable

from loguru import logger


@runtime_checkable
class DataFeed(Protocol):
    """数据源协议"""

    name: str

    def get_quotes(self, codes: list[str]) -> list[dict]:
        """批量获取实时行情，codes 为纯代码列表如 ["600000", "128025"]"""
        ...

    def get_kline(self, code: str, klt: int = 101, count: int = 500) -> list[dict]:
        """获取K线数据
        klt: 5/15/30/60/101(日)/102(周)/103(月)
        """
        ...

    def is_available(self) -> bool:
        ...


class DataFeedManager:
    """统一数据源管理器 - 按优先级自动切换

    优先级: pytdx → eastmoney → sina
    任一源失败自动降级到下一个
    """

    def __init__(self):
        self.feeds: list[DataFeed] = []
        self._health: dict[str, bool] = {}
        self._last_check: dict[str, float] = {}
        self._check_interval = 60  # 健康检查间隔秒

    def register(self, feed: DataFeed):
        """注册数据源（按注册顺序即优先级）"""
        self.feeds.append(feed)
        self._health[feed.name] = True
        self._last_check[feed.name] = 0
        logger.info(f"DataFeed registered: {feed.name}")

    def _should_check(self, name: str) -> bool:
        return time.time() - self._last_check.get(name, 0) > self._check_interval

    def _mark_down(self, name: str):
        self._health[name] = False
        self._last_check[name] = time.time()
        logger.warning(f"DataFeed {name} marked DOWN")

    def _mark_up(self, name: str):
        if not self._health.get(name, True):
            logger.info(f"DataFeed {name} recovered")
        self._health[name] = True
        self._last_check[name] = time.time()

    def get_quotes(self, codes: list[str]) -> list[dict]:
        """按优先级获取实时行情"""
        for feed in self.feeds:
            # 跳过已知故障源（除非到了重试时间）
            if not self._health.get(feed.name, True) and not self._should_check(feed.name):
                continue
            try:
                if not feed.is_available():
                    self._mark_down(feed.name)
                    continue
                result = feed.get_quotes(codes)
                if result:
                    self._mark_up(feed.name)
                    return result
            except Exception as e:
                logger.error(f"DataFeed {feed.name} get_quotes error: {e}")
                self._mark_down(feed.name)
        return []

    def get_kline(self, code: str, klt: int = 101, count: int = 500) -> list[dict]:
        """按优先级获取K线"""
        for feed in self.feeds:
            if not self._health.get(feed.name, True) and not self._should_check(feed.name):
                continue
            try:
                if not feed.is_available():
                    self._mark_down(feed.name)
                    continue
                result = feed.get_kline(code, klt, count)
                if result:
                    self._mark_up(feed.name)
                    return result
            except Exception as e:
                logger.error(f"DataFeed {feed.name} get_kline error: {e}")
                self._mark_down(feed.name)
        return []

    def get_active_feed(self) -> str | None:
        """返回当前活跃的数据源名"""
        for feed in self.feeds:
            if self._health.get(feed.name, True):
                return feed.name
        return None

    def get_status(self) -> list[dict]:
        """各数据源状态"""
        return [
            {"name": f.name, "healthy": self._health.get(f.name, True)}
            for f in self.feeds
        ]


# ---------- pytdx 适配器 ----------

class TdxFeed:
    """pytdx 数据源适配器"""

    name = "pytdx"

    def __init__(self):
        from .tdx_realtime import get_tdx_engine
        self._engine = get_tdx_engine()

    def is_available(self) -> bool:
        return self._engine.connected

    @staticmethod
    def _parse_market(code: str) -> int:
        if code.startswith(("6", "11")):
            return 1  # SH
        return 0  # SZ

    def get_quotes(self, codes: list[str]) -> list[dict]:
        stock_list = [(self._parse_market(c), c) for c in codes]
        all_quotes = []
        for i in range(0, len(stock_list), 80):
            batch = stock_list[i:i + 80]
            all_quotes.extend(self._engine.get_quotes(batch))
        return all_quotes

    def get_kline(self, code: str, klt: int = 101, count: int = 500) -> list[dict]:
        # klt 映射到 pytdx category
        klt_map = {5: 0, 15: 1, 30: 2, 60: 3, 101: 9, 102: 5, 103: 6}
        category = klt_map.get(klt, 9)
        market = self._parse_market(code)
        return self._engine.get_kline(market, code, category, 0, count)


# ---------- 全局单例 ----------

_manager: DataFeedManager | None = None


def get_feed_manager() -> DataFeedManager:
    global _manager
    if _manager is None:
        _manager = DataFeedManager()
        # 注册 pytdx (优先级1)
        try:
            _manager.register(TdxFeed())
        except Exception as e:
            logger.warning(f"TdxFeed init failed: {e}")
        # adata 数据源 (优先级2) + 新浪后备 (优先级3)
        try:
            from .http_feed import AdataFeed, SinaFeed
            _manager.register(AdataFeed())
            _manager.register(SinaFeed())
        except Exception as e:
            logger.warning(f"HTTP feed init failed: {e}")
    return _manager
