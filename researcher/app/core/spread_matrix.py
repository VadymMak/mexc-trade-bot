from __future__ import annotations

import time
from collections import deque
from typing import Callable


class SpreadMatrix:
    """
    Receives price updates from multiple collectors.
    For each symbol, tracks latest price per exchange.
    Computes spread between all exchange pairs.
    """

    def __init__(self, max_lag_ms: int = 30) -> None:
        self.max_lag_ms = max_lag_ms
        # { symbol: { exchange: (price, ts_ms) } }
        self._prices: dict[str, dict[str, tuple[float, int]]] = {}
        # { symbol: deque of (ratio, ts_ms) } for zscore
        self._history: dict[str, deque] = {}
        self._window = 300
        self._callbacks: list[Callable] = []
        # { (symbol, ex_a, ex_b): spread_dict } — latest result per directed pair
        self._latest_spreads: dict[tuple[str, str, str], dict] = {}

    async def on_price(self, symbol: str, exchange: str, price: float, ts_ms: int) -> None:
        """Called by each collector on every tick."""
        if symbol not in self._prices:
            self._prices[symbol] = {}
        self._prices[symbol][exchange] = (price, ts_ms)
        await self._compute_spreads(symbol)

    async def _compute_spreads(self, symbol: str) -> None:
        """Compute all exchange pair combinations for this symbol."""
        exchanges = list(self._prices.get(symbol, {}).items())
        if len(exchanges) < 2:
            return

        now = int(time.time() * 1000)
        results = []

        for i in range(len(exchanges)):
            for j in range(i + 1, len(exchanges)):
                ex_a, (price_a, ts_a) = exchanges[i]
                ex_b, (price_b, ts_b) = exchanges[j]

                # Skip if prices are too stale vs each other
                if abs(ts_a - ts_b) > self.max_lag_ms:
                    continue

                if price_b == 0:
                    continue

                ratio = price_a / price_b
                spread_pct = abs(ratio - 1.0) * 100
                zscore = self._compute_zscore(symbol, ratio, now)

                entry = {
                    "symbol": symbol,
                    "exchange_long": ex_b if ratio > 1 else ex_a,
                    "exchange_short": ex_a if ratio > 1 else ex_b,
                    "spread_pct": spread_pct,
                    "ratio": ratio,
                    "zscore": zscore,
                    "ts_ms": now,
                }

                # Store latest result per directed pair
                key = (symbol, entry["exchange_long"], entry["exchange_short"])
                self._latest_spreads[key] = entry

                results.append(entry)

        for r in results:
            for cb in self._callbacks:
                await cb(r)

    def _compute_zscore(self, symbol: str, ratio: float, ts_ms: int) -> float | None:
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self._window)
        self._history[symbol].append((ratio, ts_ms))
        hist = self._history[symbol]
        if len(hist) < 30:
            return None
        ratios = [r for r, _ in hist]
        mean = sum(ratios) / len(ratios)
        variance = sum((r - mean) ** 2 for r in ratios) / len(ratios)
        std = variance ** 0.5
        if std < 0.0001:
            return None
        return (ratio - mean) / std

    def add_callback(self, cb: Callable) -> None:
        """Register callback for spread updates."""
        self._callbacks.append(cb)

    def get_all_spreads(self) -> list[dict]:
        """Snapshot of latest spreads for API."""
        return list(self._latest_spreads.values())
