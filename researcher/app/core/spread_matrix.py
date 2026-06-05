from __future__ import annotations

import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .market_flow import FlowTracker

logger = logging.getLogger(__name__)


class SpreadMatrix:
    """
    Receives price updates from multiple collectors.
    For each symbol, tracks latest price per exchange.
    Computes spread between all exchange pairs.
    """

    def __init__(self, max_lag_ms: int = 5000) -> None:
        self.max_lag_ms = max_lag_ms
        # { symbol: { exchange: (price, ts_ms) } }
        self._prices: dict[str, dict[str, tuple[float, int]]] = {}
        # { symbol: deque of (ratio, ts_ms) } for zscore
        self._history: dict[str, deque] = {}
        self._window = 300
        self._callbacks: list[Callable] = []
        # { (symbol, ex_a, ex_b): spread_dict } — latest result per directed pair
        self._latest_spreads: dict[tuple[str, str, str], dict] = {}
        # optional flow tracker (tape + book metrics)
        self._flow: Optional[FlowTracker] = None
        # push config
        self._push_url: Optional[str] = None
        self._push_interval: float = 5.0

    def set_flow_tracker(self, tracker: FlowTracker) -> None:
        """Attach a FlowTracker so spread dicts include tape/book metrics."""
        self._flow = tracker

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

                # Coefficient of variation: spread_cv = std / mean
                # High cv → spread is actively oscillating → genuine arb opportunity
                # Low cv  → spread is structural/stable   → likely won't mean-revert
                spread_cv: float | None = None
                if spread_mean and spread_std and spread_mean > 0:
                    spread_cv = spread_std / spread_mean

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

                # Tape + book metrics from FlowTracker (None if not yet available)
                # Uses long-side exchange as the primary signal source
                flow_long  = self._flow.get_metrics(symbol, exch_long)  if self._flow else {}
                flow_short = self._flow.get_metrics(symbol, exch_short) if self._flow else {}

                # USD depth (top-5 levels) per exchange, then combined for arb
                if self._flow:
                    depth_bid_long,  depth_ask_long  = self._flow.get_depth_usd(symbol, exch_long)
                    depth_bid_short, depth_ask_short = self._flow.get_depth_usd(symbol, exch_short)
                else:
                    depth_bid_long = depth_ask_long = depth_bid_short = depth_ask_short = None

                depth5_bid_usd   = (depth_bid_long  or 0.0) + (depth_bid_short or 0.0) or None
                depth5_ask_usd   = (depth_ask_long  or 0.0) + (depth_ask_short or 0.0) or None
                depth5_total_usd = ((depth5_bid_usd or 0.0) + (depth5_ask_usd or 0.0)) or None
                depth_imbalance  = (
                    round((depth5_bid_usd - depth5_ask_usd) / depth5_total_usd, 4)
                    if depth5_total_usd and depth5_bid_usd is not None and depth5_ask_usd is not None
                    else None
                )

                # Always include MEXC-specific flow regardless of which side MEXC is on.
                # ScalpPaperTrader needs MEXC metrics even when MEXC is the short side.
                mexc_flow = (
                    flow_long  if exch_long  == "mexc" else
                    flow_short if exch_short == "mexc" else
                    (self._flow.get_metrics(symbol, "mexc") if self._flow else {})
                )

                entry = {
                    "symbol": symbol,
                    "exchange_long":  exch_long,
                    "exchange_short": exch_short,
                    "price_long":     price_long,
                    "price_short":    price_short,
                    "mid_price":      round((price_long + price_short) / 2, 8),
                    "spread_pct":     spread_pct,
                    "spread_bps":     round(spread_pct * 100, 4),
                    "ratio":          ratio,
                    "zscore":         zscore,
                    "spread_mean":    spread_mean,
                    "spread_std":     spread_std,
                    "spread_cv":      spread_cv,
                    "ts_ms":          now,
                    # USD depth (combined both legs)
                    "depth5_bid_usd":   depth5_bid_usd,
                    "depth5_ask_usd":   depth5_ask_usd,
                    "depth5_total_usd": depth5_total_usd,
                    "depth_imbalance":  depth_imbalance,
                    # flow features (long-side exchange) — used by PaperTrader/arb
                    # fallbacks: 0.5 = neutral buy_pressure/book_imbalance, 0.0 = no velocity data
                    "buy_pressure":    flow_long.get("buy_pressure") if flow_long.get("buy_pressure") is not None else 0.5,
                    "trade_velocity":  flow_long.get("trade_velocity") if flow_long.get("trade_velocity") is not None else 0.0,
                    "book_imbalance":  flow_long.get("book_imbalance") if flow_long.get("book_imbalance") is not None else 0.5,
                    "mm_repeat_score": flow_long.get("mm_repeat_score"),
                    # True only when tape tracker has warmed up and returned real data
                    "features_complete": (
                        flow_long.get("buy_pressure") is not None and
                        flow_long.get("book_imbalance") is not None
                    ),
                    # MEXC-specific flow — used by ScalpPaperTrader
                    "mexc_buy_pressure":    mexc_flow.get("buy_pressure") if mexc_flow.get("buy_pressure") is not None else 0.5,
                    "mexc_trade_velocity":  mexc_flow.get("trade_velocity") if mexc_flow.get("trade_velocity") is not None else 0.0,
                    "mexc_book_imbalance":  mexc_flow.get("book_imbalance") if mexc_flow.get("book_imbalance") is not None else 0.5,
                    "mexc_mm_repeat_score": mexc_flow.get("mm_repeat_score"),
                    # Spot-futures basis: (futures_price - spot_price) / spot_price
                    # Positive = futures premium, Negative = futures discount
                    # mexc_spot_basis_pct: None if spot price not yet received
                    "mexc_spot_basis_pct":  self._compute_spot_basis(symbol),
                }

                # Store latest result per directed pair
                key = (symbol, entry["exchange_long"], entry["exchange_short"])
                self._latest_spreads[key] = entry
                # Remove stale ghost entry for the opposite direction
                reverse_key = (symbol, entry["exchange_short"], entry["exchange_long"])
                self._latest_spreads.pop(reverse_key, None)

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

    def _compute_spot_basis(self, symbol: str) -> float | None:
        """
        Returns (futures_price - spot_price) / spot_price * 100  (in %).
        Positive = futures at premium to spot.
        Negative = futures at discount to spot.
        Returns None if either price is unavailable.
        """
        prices = self._prices.get(symbol, {})
        futures_price = prices.get("mexc", (None, 0))[0]
        spot_price    = prices.get("mexc_spot", (None, 0))[0]
        if not futures_price or not spot_price or spot_price == 0:
            return None
        return round((futures_price - spot_price) / spot_price * 100, 4)

    def add_callback(self, cb: Callable) -> None:
        """Register callback for spread updates."""
        self._callbacks.append(cb)

    def get_all_spreads(self) -> list[dict]:
        """Snapshot of latest spreads for API."""
        return list(self._latest_spreads.values())

    def get_notable_spreads(self, min_spread_pct: float = 0.3, top_n: int = 100) -> list[dict]:
        """
        Filtered snapshot for periodic push — only spreads worth showing on the frontend.
        Sends top N by spread_pct that exceed min_spread_pct threshold.
        Reduces push payload from ~242KB (794 pairs) to ~10-20KB (30-50 pairs).
        """
        spreads = self._latest_spreads.values()
        notable = [s for s in spreads if s.get("spread_pct", 0) >= min_spread_pct]
        notable.sort(key=lambda s: s.get("spread_pct", 0), reverse=True)
        return notable[:top_n]

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
                spreads = self.get_notable_spreads()
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
