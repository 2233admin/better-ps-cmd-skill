"""Compatibility router for strategy, research, and trading APIs."""

from fastapi import APIRouter

from .research.backtest import router as backtest_router
from .research.strategy import router as strategy_router
from .trading.crypto_live_test import router as crypto_live_test_router

router = APIRouter()
router.include_router(strategy_router)
router.include_router(backtest_router)
router.include_router(crypto_live_test_router)
