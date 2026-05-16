"""TDX 实时行情引擎 - 使用 pytdx 连接通达信行情服务器

支持多连接并行抓取，通过 asyncio.to_thread 将阻塞 IO 并发化。
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from loguru import logger
from pytdx.hq import TdxHq_API

from .tdx_parser import get_connect_servers


# 市场代码
MARKET_SH = 1  # 上海
MARKET_SZ = 0  # 深圳
MARKET_BJ = 2  # 北京

# 并行连接数
PARALLEL_CONNS = 4

# 默认服务器列表（从 connect.cfg 解析的备用）
DEFAULT_SERVERS = [
    {"host": "39.108.28.83", "port": 7709},
    {"host": "39.105.251.234", "port": 7709},
    {"host": "112.74.214.43", "port": 7709},
    {"host": "47.100.132.162", "port": 7709},
    {"host": "43.145.21.43", "port": 7709},
]


class TdxRealtimeEngine:
    """实时行情引擎，支持多连接并行"""

    def __init__(self):
        self.api = TdxHq_API(heartbeat=True, auto_retry=True)
        self.connected = False
        self.current_server = None
        self._subscribers: list[Callable] = []
        self._running = False
        self._pool_apis: list[TdxHq_API] = []
        self._pool_servers: list[dict] = []
        self._executor = ThreadPoolExecutor(max_workers=PARALLEL_CONNS)

    @staticmethod
    def _test_one_server(server: dict) -> tuple[dict, float] | None:
        """测试单台服务器连接速度"""
        host, port = server["host"], server["port"]
        if ":" in host and host.count(":") > 1:
            return None
        try:
            api = TdxHq_API()
            t0 = time.time()
            api.connect(host, port, time_out=3)
            elapsed = time.time() - t0
            api.disconnect()
            logger.debug(f"Server {host}:{port} - {elapsed:.3f}s")
            return (server, elapsed)
        except Exception as e:
            logger.debug(f"Server {host}:{port} failed: {e}")
            return None

    def _rank_servers(self) -> list[tuple[dict, float]]:
        """并行测速所有服务器"""
        servers = get_connect_servers()
        if not servers:
            servers = DEFAULT_SERVERS

        with ThreadPoolExecutor(max_workers=len(servers)) as pool:
            results = pool.map(self._test_one_server, servers)

        ranked = [r for r in results if r is not None]
        ranked.sort(key=lambda x: x[1])
        return ranked

    def connect(self) -> bool:
        """连接到行情服务器，自动选择最快的，并建立并行连接池"""
        ranked = self._rank_servers()
        if not ranked:
            logger.error("No available TDX server")
            return False

        best_server, best_time = ranked[0]
        host, port = best_server["host"], best_server["port"]
        try:
            self.api = TdxHq_API(heartbeat=True, auto_retry=True)
            self.api.connect(host, port, time_out=5)
            self.connected = True
            self.current_server = best_server
            logger.info(f"Connected to TDX server {host}:{port} ({best_time:.3f}s)")
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

        # 建立并行连接池（用不同服务器分散负载）
        self._pool_apis.clear()
        self._pool_servers.clear()
        for server, latency in ranked[:PARALLEL_CONNS]:
            try:
                api = TdxHq_API(heartbeat=True, auto_retry=True)
                api.connect(server["host"], server["port"], time_out=5)
                self._pool_apis.append(api)
                self._pool_servers.append(server)
            except Exception:
                pass
        logger.info(f"Connection pool: {len(self._pool_apis)} parallel connections")
        return True

    def disconnect(self):
        """断开所有连接"""
        if self.connected:
            self.api.disconnect()
            self.connected = False
        for api in self._pool_apis:
            try:
                api.disconnect()
            except Exception:
                pass
        self._pool_apis.clear()
        self._pool_servers.clear()

    def _ensure_connected(self):
        if not self.connected:
            self.connect()

    @staticmethod
    def _to_dicts(data) -> list[dict]:
        """将 pytdx 返回的数据转换为 list[dict]"""
        if data is None:
            return []
        if isinstance(data, list):
            return [dict(d) for d in data]
        if hasattr(data, "to_dict"):
            return data.to_dict("records")
        return list(data)

    def get_security_list(self, market: int, start: int = 0) -> list[dict]:
        """获取证券列表"""
        self._ensure_connected()
        try:
            data = self.api.get_security_list(market, start)
            return self._to_dicts(data)
        except Exception as e:
            logger.error(f"get_security_list error: {e}")
        return []

    def get_all_securities(self, market: int) -> list[dict]:
        """获取指定市场全部证券代码

        SH 市场前 ~22000 条为债券/权证等，股票和 ETF 从 ~23000 开始，
        pytdx 对空段返回 None，需要跳过继续扫描。
        """
        self._ensure_connected()
        all_stocks = []
        start = 0
        max_start = 30000 if market == MARKET_SH else 20000
        empty_streak = 0
        while start < max_start:
            batch = self.get_security_list(market, start)
            if not batch:
                empty_streak += 1
                start += 1000
                if empty_streak > 5:
                    break
                continue
            empty_streak = 0
            all_stocks.extend(batch)
            start += len(batch)
            if len(batch) < 1000:
                break
        return all_stocks

    def get_quotes(self, stock_list: list[tuple[int, str]]) -> list[dict]:
        """批量获取实时行情（单连接）

        Args:
            stock_list: [(market, code), ...] e.g. [(0, "128025"), (1, "600000")]

        Returns:
            行情数据列表
        """
        self._ensure_connected()
        try:
            data = self.api.get_security_quotes(stock_list)
            return self._to_dicts(data)
        except Exception as e:
            logger.error(f"get_quotes error: {e}")
        return []

    def _fetch_batch(self, api: TdxHq_API, batch: list[tuple[int, str]],
                     server: dict | None = None) -> list[dict]:
        """在线程中用指定连接抓一批行情，失败时自动重连"""
        try:
            data = api.get_security_quotes(batch)
            if data is None:
                return []
            if isinstance(data, list):
                return [dict(d) for d in data]
            if hasattr(data, "to_dict"):
                return data.to_dict("records")
            return list(data)
        except Exception as e:
            if server:
                logger.warning(f"Connection {server['host']} failed: {e}, reconnecting")
                try:
                    api.disconnect()
                    api.connect(server["host"], server["port"], time_out=5)
                    data = api.get_security_quotes(batch)
                    return self._to_dicts(data)
                except Exception:
                    pass
            return []

    async def get_quotes_parallel(
        self, stock_list: list[tuple[int, str]], batch_size: int = 80
    ) -> list[dict]:
        """多连接并行获取实时行情

        将请求分配到连接池中的多个连接，通过线程池并发执行。
        连接失败时自动重连重试。
        """
        self._ensure_connected()
        apis = self._pool_apis if self._pool_apis else [self.api]
        servers = self._pool_servers if self._pool_servers else [self.current_server]
        n_conns = len(apis)

        batches = [stock_list[i : i + batch_size]
                   for i in range(0, len(stock_list), batch_size)]

        conn_tasks: list[list[list[tuple[int, str]]]] = [[] for _ in range(n_conns)]
        for idx, batch in enumerate(batches):
            conn_tasks[idx % n_conns].append(batch)

        def worker(api: TdxHq_API, server: dict,
                   task_batches: list[list[tuple[int, str]]]) -> list[dict]:
            results = []
            for batch in task_batches:
                results.extend(self._fetch_batch(api, batch, server))
            return results

        loop = asyncio.get_event_loop()
        futures = []
        for i in range(n_conns):
            if conn_tasks[i]:
                futures.append(loop.run_in_executor(
                    self._executor, worker, apis[i], servers[i], conn_tasks[i]
                ))

        all_quotes = []
        for result in await asyncio.gather(*futures):
            all_quotes.extend(result)

        return all_quotes

    def get_kline(
        self,
        market: int,
        code: str,
        category: int = 9,  # 9=日线, 8=1分钟, 7=5分钟
        start: int = 0,
        count: int = 800,
    ) -> list[dict]:
        """获取K线数据

        category: 0-5分钟 1-15分钟 2-30分钟 3-1小时 4-日线 5-周线 6-月线
                  7-1分钟 8-1分钟 9-日线 10-季线 11-年线
        """
        self._ensure_connected()
        try:
            data = self.api.get_security_bars(category, market, code, start, count)
            return self._to_dicts(data)
        except Exception as e:
            logger.error(f"get_kline error: {e}")
        return []

    def get_minute_data(self, market: int, code: str) -> list[dict]:
        """获取当日分时数据"""
        self._ensure_connected()
        try:
            data = self.api.get_minute_time_data(market, code)
            return self._to_dicts(data)
        except Exception as e:
            logger.error(f"get_minute_data error: {e}")
        return []

    def get_history_minute_data(
        self, market: int, code: str, date: int
    ) -> list[dict]:
        """获取历史分时数据"""
        self._ensure_connected()
        try:
            data = self.api.get_history_minute_time_data(market, code, date)
            return self._to_dicts(data)
        except Exception as e:
            logger.error(f"get_history_minute_data error: {e}")
        return []

    def get_transaction_data(
        self, market: int, code: str, start: int = 0, count: int = 2000
    ) -> list[dict]:
        """获取逐笔成交数据"""
        self._ensure_connected()
        try:
            data = self.api.get_transaction_data(market, code, start, count)
            return self._to_dicts(data)
        except Exception as e:
            logger.error(f"get_transaction_data error: {e}")
        return []

    def get_bond_list(self) -> list[dict]:
        """获取可转债列表 (SZ: 12xxxx, SH: 11xxxx)"""
        bonds = []

        # 深市可转债 (12开头, 名称含"转债")
        sz_all = self.get_all_securities(MARKET_SZ)
        for s in sz_all:
            code = str(s.get("code", ""))
            name = str(s.get("name", ""))
            if code.startswith("12") and "转" in name:
                s["market"] = MARKET_SZ
                bonds.append(s)

        # 沪市可转债 (11开头, 名称含"转债")
        sh_all = self.get_all_securities(MARKET_SH)
        for s in sh_all:
            code = str(s.get("code", ""))
            name = str(s.get("name", ""))
            if code.startswith("11") and "转" in name:
                s["market"] = MARKET_SH
                bonds.append(s)

        logger.info(f"Found {len(bonds)} convertible bonds")
        return bonds

    def get_bond_quotes(self, bonds: list[dict] | None = None) -> list[dict]:
        """获取可转债实时行情"""
        if bonds is None:
            bonds = self.get_bond_list()

        stock_list = [(b["market"], b["code"]) for b in bonds]

        # pytdx 一次最多请求 80 只
        all_quotes = []
        for i in range(0, len(stock_list), 80):
            batch = stock_list[i : i + 80]
            quotes = self.get_quotes(batch)
            all_quotes.extend(quotes)

        # 可转债价格在 pytdx 中需要除以 10（decimal_point=3）
        for q in all_quotes:
            for key in ("price", "open", "high", "low", "last_close",
                        "bid1", "bid2", "bid3", "bid4", "bid5",
                        "ask1", "ask2", "ask3", "ask4", "ask5"):
                if key in q and q[key]:
                    q[key] = q[key] / 100

        return all_quotes

    async def start_realtime_loop(
        self, stock_list: list[tuple[int, str]], interval: float = 0.5
    ):
        """启动实时行情循环推送

        Args:
            stock_list: 订阅的证券列表
            interval: 推送间隔(秒)
        """
        self._running = True
        logger.info(f"Starting realtime loop for {len(stock_list)} securities")

        while self._running:
            try:
                # 使用并行连接池获取
                all_quotes = await self.get_quotes_parallel(stock_list)

                # GPU因子引擎更新+计算
                try:
                    from app.data.gpu_factors import get_gpu_factor_engine
                    gpu = get_gpu_factor_engine()
                    gpu.update(all_quotes)
                    gpu.compute()
                except Exception as e:
                    logger.debug(f"GPU factor update: {e}")

                # 通知所有订阅者
                for callback in self._subscribers:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(all_quotes)
                        else:
                            callback(all_quotes)
                    except Exception as e:
                        logger.error(f"Subscriber callback error: {e}")

                await asyncio.sleep(interval)
            except Exception as e:
                logger.error(f"Realtime loop error: {e}")
                await asyncio.sleep(1)

    def stop_realtime_loop(self):
        """停止实时行情循环"""
        self._running = False

    def subscribe(self, callback: Callable):
        """订阅实时行情回调"""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        """取消订阅"""
        self._subscribers.remove(callback)


# 全局单例
_engine: TdxRealtimeEngine | None = None


def get_tdx_engine() -> TdxRealtimeEngine:
    global _engine
    if _engine is None:
        _engine = TdxRealtimeEngine()
    return _engine
