from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger(__name__)


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
        # push config
        self._push_url: Optional[str] = None
        self._push_interval: float = 5.0

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
                zscore, spread_mean, spread_std = self._compute_zscore(symbol, ratio, now)

                if ratio > 1:
                    # ex_a is more expensive → short ex_a, long ex_b
                    price_long  = price_b
                    price_short = price_a
                    exch_long   = ex_b
                    exch_short  = ex_a
                else:
                    price_long  = price_a
                    price_short = price_b
                    exch_long   = ex_a
                    exch_short  = ex_b

                entry = {
                    "symbol": symbol,
                    "exchange_long":  exch_long,
                    "exchange_short": exch_short,
                    "price_long":     price_long,
                    "price_short":    price_short,
                    "spread_pct":     spread_pct,
                    "ratio":          ratio,
                    "zscore":         zscore,
                    "spread_mean":    spread_mean,
                    "spread_std":     spread_std,
                    "ts_ms":          now,
                }

                # Store latest result per directed pair
                key = (symbol, entry["exchange_long"], entry["exchange_short"])
                self._latest_spreads[key] = entry

                results.append(entry)

        for r in results:
            for cb in self._callbacks:
                await cb(r)

    def _compute_zscore(
        self, symbol: str, ratio: float, ts_ms: int
    ) -> tuple[float | None, float | None, float | None]:
        """Returns (zscore, spread_mean_pct, spread_std_pct).
        spread_mean/std are expressed in same units as spread_pct (percentage points).
        """
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self._window)
        self._history[symbol].append((ratio, ts_ms))
        hist = self._history[symbol]
        if len(hist) < 30:
            return None, None, None
        ratios = [r for r, _ in hist]
        mean = sum(ratios) / len(ratios)
        variance = sum((r - mean) ** 2 for r in ratios) / len(ratios)
        std = variance ** 0.5
        if std < 0.0001:
            return None, abs(mean - 1.0) * 100, std * 100
        zscore = (ratio - mean) / std
        # Convert ratio mean/std to spread percentage-point units
        spread_mean_pct = abs(mean - 1.0) * 100
        spread_std_pct  = std * 100
        return zscore, spread_mean_pct, spread_std_pct

    def add_callback(self, cb: Callable) -> None:
        """Register callback for spread updates."""
        self._callbacks.append(cb)

    def get_all_spreads(self) -> list[dict]:
        """Snapshot of latest spreads for API."""
        return list(self._latest_spreads.values())

    def set_push_url(self, url: str, interval_s: float = 5.0) -> None:
        """Configure periodic push of spread snapshots to the trading bot."""
        self._push_url = url
        self._push_interval = interval_s

    async def push_loop(self) -> None:
        """
        Background task: POST get_all_spreads() to _push_url every _push_interval seconds.
        No-op if _push_url is not set.
        """
        if not self._push_url:
            # Nothing to push — just park forever so gather() doesn't exit
            import asyncio
            await asyncio.Event().wait()
            return

        import asyncio
        import json
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp not installed — spread push disabled")
            await asyncio.Event().wait()
            return

        logger.info("[SpreadMatrix] Push loop started → %s every %.0fs", self._push_url, self._push_interval)

        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(self._push_interval)
                spreads = self.get_all_spreads()
                if not spreads:
                    continue
                try:
                    async with session.post(
                        self._push_url,
                        data=json.dumps(spreads),
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status >= 400:
                            logger.warning("[SpreadMatrix] Push failed: HTTP %d", resp.status)
                except Exception as exc:
                    logger.warning("[SpreadMatrix] Push error: %r", exc)
