# app/market_data/binance_ws_stub.py
from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List, Optional


class BinanceWSClient:
    """
    Заглушка WebSocket-клиента Binance на Этапе 1.
    Ничего не подключает, ничего не публикует; только совместимый интерфейс.
    """
    def __init__(self, symbols: List[str], channels: Optional[List[str]] = None) -> None:
        self.symbols = [s.strip().upper() for s in symbols if s and s.strip()]
        self.channels = channels or ["bookTicker"]
        self._stopping = False

    async def run(self) -> None:
        # имитируем «жизненный цикл» без подключения
        try:
            while not self._stopping:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        self._stopping = True

    # иногда удобно иметь совместимый генератор подписки
    async def connect(self, symbols: List[str], interval_ms: int = 500) -> AsyncGenerator[None, None]:
        # пустой async-генератор, чтобы текущие места вызова не падали
        try:
            while not self._stopping:
                await asyncio.sleep(interval_ms / 1000.0)
                yield None
        except asyncio.CancelledError:
            return
