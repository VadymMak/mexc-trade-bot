"""
SpreadMatrix — in-memory price book for cross-exchange spread computation.

Responsibilities:
  1. Store the latest mark price per (symbol, exchange).
  2. On every new tick, compute pairwise spreads for that symbol.
  3. Maintain a rolling window of spread history to compute z-scores.
  4. Return SpreadSnapshot objects only when spread >= MIN_SPREAD_PCT.

Usage:
    matrix = SpreadMatrix()
    matrix.on_price  ← pass as callback to all collectors via set_callback()
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from statistics import mean, stdev
from typing import Dict, Optional, Tuple

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Tick:
    exchange: str
    price: float
    ts_ms: int


@dataclass(slots=True)
class SpreadSnapshot:
    symbol: str
    exchange_long: str    # buy here (lower price)
    exchange_short: str   # sell here (higher price)
    long_price: float
    short_price: float
    spread_pct: float     # (short - long) / long
    zscore: Optional[float]
    ts_ms: int


# Internal key for per-pair history
_HistKey = Tuple[str, str, str]   # (symbol, exchange_long, exchange_short)


class SpreadMatrix:
    """Thread-safe (asyncio.Lock) price matrix with z-score computation."""

    def __init__(self) -> None:
        # Latest tick per (symbol → exchange → Tick)
        self._prices: Dict[str, Dict[str, Tick]] = {}
        # Rolling spread history per direction
        self._history: Dict[_HistKey, deque[float]] = {}

        self._window: int = settings.SPREAD_WINDOW_TICKS
        self._min_spread: float = settings.MIN_SPREAD_PCT
        self._max_lag_ms: int = settings.MAX_SPREAD_LAG_MS
        self._lock = asyncio.Lock()

    async def on_price(
        self, symbol: str, exchange: str, price: float, ts_ms: int
    ) -> list[SpreadSnapshot]:
        """
        Called by every collector tick.
        Updates internal state and returns triggered SpreadSnapshot list
        (only entries where spread >= MIN_SPREAD_PCT).
        """
        async with self._lock:
            book = self._prices.setdefault(symbol, {})
            book[exchange] = Tick(exchange=exchange, price=price, ts_ms=ts_ms)
            return self._compute(symbol, ts_ms)

    def snapshot_all(self) -> list[SpreadSnapshot]:
        """
        Synchronous read of latest spread for every known pair.
        Used for the API/SSE response layer.
        """
        results: list[SpreadSnapshot] = []
        for symbol in list(self._prices):
            results.extend(self._compute(symbol, int(time.time() * 1000)))
        return results

    # ─── private ───

    def _compute(self, symbol: str, now_ms: int) -> list[SpreadSnapshot]:
        book = self._prices.get(symbol, {})
        exchanges = list(book)
        if len(exchanges) < 2:
            return []

        snapshots: list[SpreadSnapshot] = []

        for i, ex_a in enumerate(exchanges):
            for ex_b in exchanges[i + 1:]:
                tick_a = book[ex_a]
                tick_b = book[ex_b]

                # Reject stale data: timestamps too far apart
                if abs(tick_a.ts_ms - tick_b.ts_ms) > self._max_lag_ms:
                    continue

                # Evaluate both directions; emit the positive one
                for ex_long, ex_short in ((ex_a, ex_b), (ex_b, ex_a)):
                    p_long = book[ex_long].price
                    p_short = book[ex_short].price
                    if p_long <= 0 or p_short <= 0:
                        continue

                    spread_pct = (p_short - p_long) / p_long

                    key: _HistKey = (symbol, ex_long, ex_short)
                    hist = self._history.setdefault(key, deque(maxlen=self._window))
                    hist.append(spread_pct)

                    zscore: Optional[float] = None
                    if len(hist) >= 30:
                        mu = mean(hist)
                        sigma = stdev(hist)
                        if sigma > 0:
                            zscore = (spread_pct - mu) / sigma

                    if spread_pct >= self._min_spread:
                        snapshots.append(SpreadSnapshot(
                            symbol=symbol,
                            exchange_long=ex_long,
                            exchange_short=ex_short,
                            long_price=p_long,
                            short_price=p_short,
                            spread_pct=spread_pct,
                            zscore=zscore,
                            ts_ms=now_ms,
                        ))

        return snapshots
