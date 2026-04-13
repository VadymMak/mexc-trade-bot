"""
researcher/app/live/base.py

Universal FuturesClient Protocol — every exchange implements this.
Adding a new exchange = implement these 5 methods, nothing else changes.

Supported exchanges so far:
  - MexcFutures   (contract.mexc.com)
  - GateFutures   (api.gateio.ws /futures/usdt/)

Future exchanges just add a new file + implement FuturesClient.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@dataclass
class OrderResult:
    ok: bool
    order_id: Optional[str] = None
    filled_qty: float = 0.0
    avg_price: float = 0.0
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class PositionInfo:
    symbol: str
    qty: float          # >0 long, <0 short, 0 = no position
    avg_price: float
    unrealized_pnl: float = 0.0
    leverage: int = 1


@runtime_checkable
class FuturesClient(Protocol):
    """
    Minimal interface every futures exchange must implement.
    All methods are async. symbol format: 'BTC_USDT'.
    """

    exchange_name: str  # e.g. "mexc", "gate"

    async def open_long(
        self,
        symbol: str,
        usdt_size: float,
        leverage: int = 1,
    ) -> OrderResult:
        """Open a LONG position. usdt_size = notional in USDT."""
        ...

    async def open_short(
        self,
        symbol: str,
        usdt_size: float,
        leverage: int = 1,
    ) -> OrderResult:
        """Open a SHORT position. usdt_size = notional in USDT."""
        ...

    async def close_long(
        self,
        symbol: str,
        qty: Optional[float] = None,  # None = close full position
    ) -> OrderResult:
        """Close an existing LONG position (market order)."""
        ...

    async def close_short(
        self,
        symbol: str,
        qty: Optional[float] = None,
    ) -> OrderResult:
        """Close an existing SHORT position (market order)."""
        ...

    async def get_position(self, symbol: str) -> PositionInfo:
        """Return current position for symbol (qty=0 if none)."""
        ...

    async def aclose(self) -> None:
        """Release HTTP sessions / connections."""
        ...
