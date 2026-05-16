"""Multi-market quantitative research terminal - FastAPI backend."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .api.ashare.market import router as market_router
from .api.ashare.bond import router as bond_router
from .api.portfolio import router as portfolio_router
from .api.strategy_api import router as strategy_router
from .api.order import router as order_router
from .api.ai_api import router as ai_router
from .api.macro_api import router as macro_router
from .api.crypto.okx_api import router as okx_router
from .api.trading.paper import router as paper_trading_router
from .ws.realtime import (
    ws_quotes_handler,
    ws_trades_handler,
    ws_strategy_handler,
    ws_alerts_handler,
    ws_manager,
)
from .data.tdx_realtime import get_tdx_engine
from .data.feed_manager import get_feed_manager
from .data.store import get_store
from .strategy.engine import get_strategy_engine
from .trading.executor import get_executor
from .trade.account_reader import get_account_reader_manager


# 后台行情推送任务
async def _quote_push_loop():
    """行情推送后台任务: 通过 DataFeedManager 获取行情并推送到 WebSocket"""
    engine = get_tdx_engine()
    feed_mgr = get_feed_manager()
    strategy_engine = get_strategy_engine()
    executor = get_executor()

    # 获取可转债列表（仍通过 pytdx 获取代码表）
    bonds = engine.get_bond_list()
    if not bonds:
        logger.warning("No bonds found, quote push disabled")
        return

    codes = [b["code"] for b in bonds[:50]]
    logger.info(f"Quote push started for {len(codes)} securities via DataFeedManager")

    while True:
        try:
            if ws_manager.get_subscriber_count("quotes") > 0:
                all_quotes = feed_mgr.get_quotes(codes)

                if all_quotes:
                    await ws_manager.broadcast("quotes", {
                        "type": "quotes",
                        "data": all_quotes,
                    })
                    executor.update_prices(all_quotes)
                    strategy_engine.on_quotes(all_quotes)

            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Quote push error: {e}")
            await asyncio.sleep(2)


async def _account_sync_loop():
    """账户同步后台任务: 定期从交易软件同步真实持仓"""
    executor = get_executor()
    acct_mgr = get_account_reader_manager()

    while True:
        try:
            reader = acct_mgr.get_primary()
            if reader and reader.is_available():
                positions = reader.get_positions()
                if positions:
                    executor.sync_real_positions(positions)
            await asyncio.sleep(30)  # 30秒同步一次
        except Exception as e:
            logger.error(f"Account sync error: {e}")
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("=== k-atana research terminal started ===")

    # 初始化数据存储
    store = get_store()
    logger.info("DuckDB initialized")

    # 连接通达信行情服务器
    engine = get_tdx_engine()
    connected = engine.connect()
    if connected:
        logger.info(f"TDX connected to {engine.current_server}")
    else:
        logger.warning("TDX connection failed, will retry on demand")

    # 初始化 DataFeedManager（pytdx + HTTP 后备）
    feed_mgr = get_feed_manager()
    logger.info(f"DataFeedManager: {[f['name'] for f in feed_mgr.get_status()]}")

    # 启动行情推送后台任务
    push_task = asyncio.create_task(_quote_push_loop())
    # 启动账户同步后台任务
    sync_task = asyncio.create_task(_account_sync_loop())

    yield

    # 清理
    logger.info("Shutting down...")
    push_task.cancel()
    sync_task.cancel()
    engine.disconnect()
    store.close()


app = FastAPI(
    title="k-atana research terminal",
    description="Multi-market quantitative research terminal with controlled trading tests",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST API
app.include_router(market_router, prefix="/api/v1")
app.include_router(bond_router, prefix="/api/v1")
app.include_router(portfolio_router, prefix="/api/v1")
app.include_router(strategy_router, prefix="/api/v1")
app.include_router(order_router, prefix="/api/v1")
app.include_router(ai_router, prefix="/api/v1")
app.include_router(macro_router, prefix="/api/v1")
app.include_router(okx_router, prefix="/api/v1")
app.include_router(paper_trading_router, prefix="/api/v1")

# WebSocket
app.websocket("/ws/quotes")(ws_quotes_handler)
app.websocket("/ws/trades")(ws_trades_handler)
app.websocket("/ws/strategy")(ws_strategy_handler)
app.websocket("/ws/alerts")(ws_alerts_handler)


@app.get("/")
async def root():
    return {
        "name": "k-atana research terminal",
        "version": "0.1.0",
        "status": "running",
        "endpoints": {
            "api": "/api/v1",
            "docs": "/docs",
            "ws_quotes": "ws://localhost:8000/ws/quotes",
            "ws_trades": "ws://localhost:8000/ws/trades",
        },
    }


@app.get("/health")
async def health():
    engine = get_tdx_engine()
    feed_mgr = get_feed_manager()
    return {
        "status": "ok",
        "tdx_connected": engine.connected,
        "tdx_server": engine.current_server,
        "active_feed": feed_mgr.get_active_feed(),
        "feeds": feed_mgr.get_status(),
    }
