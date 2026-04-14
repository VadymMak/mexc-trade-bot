"""
researcher/app/live/mock_futures.py

Mock FuturesClient — used as a placeholder when one exchange is unavailable
(e.g. MEXC during Gate testnet testing).

All methods succeed instantly without placing real orders.
Useful for testing the ArbLiveExecutor flow end-to-end on a single real exchange.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .base import OrderResult, PositionInfo

logger = logging.getLogger(__name__)


class MockFutures:
    """
    Drop-in FuturesClient that logs calls and returns success without touching any exchange.

    Usage: replace one leg with MockFutures() to test the other leg on testnet.
    """

    exchange_name = "mock"

    def __init__(self, name: str = "mock") -> None:
        self.exchange_name = name

    async def __aenter__(self) -> "MockFutures":
        logger.info("[%s] MockFutures session opened", self.exchange_name)
        return self

    async def __aexit__(self, *_: Any) -> None:
        logger.info("[%s] MockFutures session closed", self.exchange_name)

    async def aclose(self) -> None:
        pass

    async def open_long(self, symbol: str, usdt_size: float, leverage: int = 1) -> OrderResult:
        logger.info("[%s] MOCK open_long %s size=%.2f", self.exchange_name, symbol, usdt_size)
        return OrderResult(ok=True, order_id="mock-long-001", filled_qty=usdt_size)

    async def open_short(self, symbol: str, usdt_size: float, leverage: int = 1) -> OrderResult:
        logger.info("[%s] MOCK open_short %s size=%.2f", self.exchange_name, symbol, usdt_size)
        return OrderResult(ok=True, order_id="mock-short-001", filled_qty=usdt_size)

    async def close_long(self, symbol: str, qty: Optional[float] = None) -> OrderResult:
        logger.info("[%s] MOCK close_long %s qty=%s", self.exchange_name, symbol, qty)
        return OrderResult(ok=True, order_id="mock-close-long-001", filled_qty=qty or 0)

    async def close_short(self, symbol: str, qty: Optional[float] = None) -> OrderResult:
        logger.info("[%s] MOCK close_short %s qty=%s", self.exchange_name, symbol, qty)
        return OrderResult(ok=True, order_id="mock-close-short-001", filled_qty=qty or 0)

    async def get_position(self, symbol: str) -> PositionInfo:
        return PositionInfo(symbol=symbol, qty=0.0, avg_price=0.0)
