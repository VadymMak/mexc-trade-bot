from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

# Callback signature: cb(symbol, exchange_name, mark_price, ts_ms)
Callback = Callable[[str, str, float, int], Awaitable[None]]


class BaseCollector:
    """Abstract base for exchange mark-price collectors."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._callback: Optional[Callback] = None

    def set_callback(self, cb: Callback) -> None:
        """Register the callback that receives every price tick."""
        self._callback = cb

    async def connect(self, symbols: list[str]) -> None:
        """Open WebSocket and start streaming. Non-blocking (spawns a task)."""
        raise NotImplementedError

    async def disconnect(self) -> None:
        """Gracefully close the WebSocket and cancel internal tasks."""
        raise NotImplementedError

    async def _notify(self, symbol: str, price: float, ts_ms: int) -> None:
        """Forward a tick to the registered callback."""
        if self._callback:
            await self._callback(symbol, self.name, price, ts_ms)
