"""
researcher/app/live/arb_executor.py

ArbLiveExecutor — drop-in replacement for PaperTrader.
Receives the same on_spread(data) calls, opens real two-leg futures positions.

Leg safety (most important part):
  - Both legs opened with asyncio.gather() — near-simultaneous
  - If one leg fails → immediately close the successful leg (rollback)
  - Rollback failure → logged as critical, position tracked in _orphans for retry

Adding new strategies:
  - Create a new file (e.g. grid_executor.py) with an on_spread() method
  - Plug into main.py the same way as ArbLiveExecutor
  - No changes to exchange clients needed
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from ..config import Settings
from ..db.neon_db import NeonDB
from .base import FuturesClient, OrderResult
from .mexc_futures import MexcFutures
from .gate_futures import GateFutures

logger = logging.getLogger(__name__)


@dataclass
class _LivePosition:
    """Tracks a single open two-leg arb position."""
    symbol:        str
    ex_long:       str
    ex_short:      str
    entry_spread:  float
    entry_zscore:  float
    deal_size:     float
    opened_ms:     int
    long_order_id: Optional[str] = None
    short_order_id: Optional[str] = None
    # qty filled on each leg (for close)
    long_qty:      float = 0.0
    short_qty:     float = 0.0


class ArbLiveExecutor:
    """
    Live cross-exchange arb executor.

    Mirrors PaperTrader.on_spread() interface so main.py can swap them
    with a single flag: LIVE_TRADING=true.

    Exchange clients are injected — easy to add Gate+KuCoin later
    without touching this class.
    """

    def __init__(
        self,
        db: NeonDB,
        settings: Settings,
        clients: Dict[str, FuturesClient],   # {"mexc": MexcFutures, "gate": GateFutures, ...}
    ) -> None:
        self.db       = db
        self.settings = settings
        self.clients  = clients               # exchange_name → client

        # {(symbol, ex_long, ex_short): _LivePosition}
        self._open: Dict[Tuple[str, str, str], _LivePosition] = {}

        # Orphaned legs that failed to close — retry on next tick
        self._orphans: list[dict] = []

        # Session stats
        self._total_opened  = 0
        self._total_closed  = 0
        self._total_net_pnl = 0.0

    # ── Main entry point (same signature as PaperTrader.on_spread) ─────────────

    async def on_spread(self, data: dict) -> None:
        symbol    = data["symbol"]
        ex_long   = data["exchange_long"]
        ex_short  = data["exchange_short"]
        zscore    = data.get("zscore")
        spread_pct = data["spread_pct"]
        spread_cv  = data.get("spread_cv")
        ts_ms      = data.get("ts_ms", int(time.time() * 1000))
        trade_velocity = data.get("trade_velocity")

        key = (symbol, ex_long, ex_short)

        # Retry any orphaned close attempts first
        if self._orphans:
            await self._retry_orphans()

        if key in self._open:
            await self._maybe_close(key, zscore, spread_pct, ts_ms)
        else:
            await self._maybe_open(
                key, symbol, ex_long, ex_short,
                zscore, spread_pct, spread_cv, ts_ms, trade_velocity,
            )

    # ── Open ───────────────────────────────────────────────────────────────────

    async def _maybe_open(
        self,
        key: Tuple[str, str, str],
        symbol: str,
        ex_long: str,
        ex_short: str,
        zscore: Optional[float],
        spread_pct: float,
        spread_cv: Optional[float],
        ts_ms: int,
        trade_velocity: Optional[float],
    ) -> None:
        # ── Entry guards (same as PaperTrader) ──────────────────────────────
        if spread_pct > self.settings.MAX_SPREAD_PCT:
            return
        if spread_cv is not None and spread_cv < self.settings.MIN_SPREAD_CV:
            return
        allowed = self.settings.trading_exchanges_set
        if ex_long not in allowed or ex_short not in allowed:
            return
        if symbol in self.settings.blacklisted_set:
            return

        zscore_entry = (
            zscore is not None
            and abs(zscore) >= self.settings.ZSCORE_THRESHOLD
            and spread_pct >= self.settings.MIN_SPREAD_PCT * 100
        )
        if not zscore_entry:
            return

        # Check we have clients for both exchanges
        if ex_long not in self.clients or ex_short not in self.clients:
            logger.warning("No live client for %s or %s — skipping %s", ex_long, ex_short, symbol)
            return

        deal_size = self._dynamic_deal_size(spread_pct, trade_velocity)

        # ── Open both legs simultaneously ────────────────────────────────────
        logger.info(
            "[LIVE OPEN] %s %s/↑ %s/↓  spread=%.3f%%  z=%+.2f  size=%.0f USDT",
            symbol, ex_long, ex_short, spread_pct, zscore, deal_size,
        )

        long_coro  = self.clients[ex_long].open_long(symbol, deal_size)
        short_coro = self.clients[ex_short].open_short(symbol, deal_size)

        long_res, short_res = await asyncio.gather(
            long_coro, short_coro, return_exceptions=True
        )

        long_ok  = isinstance(long_res, OrderResult) and long_res.ok
        short_ok = isinstance(short_res, OrderResult) and short_res.ok

        # ── Leg risk: rollback if one leg failed ─────────────────────────────
        if long_ok and not short_ok:
            short_err = short_res if isinstance(short_res, Exception) else getattr(short_res, "error", "?")
            logger.error("[LIVE LEG-RISK] short failed on %s: %s — rolling back long", ex_short, short_err)
            await self._safe_close_long(ex_long, symbol, long_res.filled_qty)
            return

        if short_ok and not long_ok:
            long_err = long_res if isinstance(long_res, Exception) else getattr(long_res, "error", "?")
            logger.error("[LIVE LEG-RISK] long failed on %s: %s — rolling back short", ex_long, long_err)
            await self._safe_close_short(ex_short, symbol, short_res.filled_qty)
            return

        if not long_ok and not short_ok:
            logger.error("[LIVE] Both legs failed for %s — skipping", symbol)
            return

        # ── Both legs opened ─────────────────────────────────────────────────
        self._open[key] = _LivePosition(
            symbol=symbol,
            ex_long=ex_long,
            ex_short=ex_short,
            entry_spread=spread_pct,
            entry_zscore=zscore,
            deal_size=deal_size,
            opened_ms=ts_ms,
            long_order_id=long_res.order_id,
            short_order_id=short_res.order_id,
            long_qty=long_res.filled_qty,
            short_qty=short_res.filled_qty,
        )
        self._total_opened += 1
        logger.info(
            "[LIVE OPEN OK] %s  long_order=%s  short_order=%s",
            symbol, long_res.order_id, short_res.order_id,
        )

    # ── Close ──────────────────────────────────────────────────────────────────

    async def _maybe_close(
        self,
        key: Tuple[str, str, str],
        zscore: Optional[float],
        spread_pct: float,
        ts_ms: int,
    ) -> None:
        pos   = self._open[key]
        entry = pos.entry_spread
        hold  = max(0, (ts_ms - pos.opened_ms) // 1000)

        reason: Optional[str] = None

        if spread_pct <= entry * self.settings.TAKE_PROFIT_RATIO:
            reason = "TAKE_PROFIT"
        elif (
            zscore is not None
            and abs(zscore) < self.settings.ZSCORE_EXIT
            and hold >= self.settings.ZSCORE_REVERT_MIN_HOLD_SECONDS
        ):
            reason = "ZSCORE_REVERT"
        elif spread_pct >= entry * self.settings.STOP_LOSS_RATIO:
            reason = "STOP_LOSS"
        elif hold >= self.settings.MAX_HOLD_SECONDS:
            reason = "TIME_STOP"

        if reason is None:
            return

        logger.info(
            "[LIVE CLOSE %s] %s  spread=%.3f%%→%.3f%%  hold=%ds",
            reason, pos.symbol, entry, spread_pct, hold,
        )

        del self._open[key]

        # Close both legs simultaneously
        long_res, short_res = await asyncio.gather(
            self.clients[pos.ex_long].close_long(pos.symbol, pos.long_qty),
            self.clients[pos.ex_short].close_short(pos.symbol, pos.short_qty),
            return_exceptions=True,
        )

        long_ok  = isinstance(long_res, OrderResult) and long_res.ok
        short_ok = isinstance(short_res, OrderResult) and short_res.ok

        if not long_ok:
            err = long_res if isinstance(long_res, Exception) else getattr(long_res, "error", "?")
            logger.critical("[LIVE ORPHAN] Failed to close LONG %s on %s: %s", pos.symbol, pos.ex_long, err)
            self._orphans.append({"side": "long", "exchange": pos.ex_long, "symbol": pos.symbol, "qty": pos.long_qty})

        if not short_ok:
            err = short_res if isinstance(short_res, Exception) else getattr(short_res, "error", "?")
            logger.critical("[LIVE ORPHAN] Failed to close SHORT %s on %s: %s", pos.symbol, pos.ex_short, err)
            self._orphans.append({"side": "short", "exchange": pos.ex_short, "symbol": pos.symbol, "qty": pos.short_qty})

        self._total_closed += 1

        gross_pnl = pos.deal_size * (entry - spread_pct) / 100.0
        logger.info(
            "[LIVE CLOSE OK] %s  gross_pnl≈%+.4f  long_ok=%s  short_ok=%s",
            pos.symbol, gross_pnl, long_ok, short_ok,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _dynamic_deal_size(self, spread_pct: float, trade_velocity: Optional[float] = None) -> float:
        """Same tiered logic as PaperTrader._dynamic_deal_size."""
        base = self.settings.PAPER_DEAL_SIZE_USDT
        if spread_pct >= self.settings.MM_TIER3_SPREAD_PCT:
            size = base * self.settings.MM_TIER3_MULT
        elif spread_pct >= self.settings.MM_TIER2_SPREAD_PCT:
            size = base * self.settings.MM_TIER2_MULT
        elif spread_pct >= self.settings.MM_TIER1_SPREAD_PCT:
            size = base * self.settings.MM_TIER1_MULT
        else:
            size = base

        if trade_velocity is not None and trade_velocity > 0:
            usdt_per_min = trade_velocity * 7.5
            volume_cap   = max(base, usdt_per_min * 0.30)
            size         = min(size, volume_cap)
        return size

    async def _safe_close_long(self, exchange: str, symbol: str, qty: float) -> None:
        try:
            res = await self.clients[exchange].close_long(symbol, qty)
            if not res.ok:
                logger.critical("[LIVE ORPHAN] Rollback long failed %s %s: %s", exchange, symbol, res.error)
                self._orphans.append({"side": "long", "exchange": exchange, "symbol": symbol, "qty": qty})
        except Exception as e:
            logger.critical("[LIVE ORPHAN] Rollback long exception %s %s: %s", exchange, symbol, e)
            self._orphans.append({"side": "long", "exchange": exchange, "symbol": symbol, "qty": qty})

    async def _safe_close_short(self, exchange: str, symbol: str, qty: float) -> None:
        try:
            res = await self.clients[exchange].close_short(symbol, qty)
            if not res.ok:
                logger.critical("[LIVE ORPHAN] Rollback short failed %s %s: %s", exchange, symbol, res.error)
                self._orphans.append({"side": "short", "exchange": exchange, "symbol": symbol, "qty": qty})
        except Exception as e:
            logger.critical("[LIVE ORPHAN] Rollback short exception %s %s: %s", exchange, symbol, e)
            self._orphans.append({"side": "short", "exchange": exchange, "symbol": symbol, "qty": qty})

    async def _retry_orphans(self) -> None:
        """Retry closing orphaned legs from previous failures."""
        if not self._orphans:
            return
        remaining = []
        for o in self._orphans:
            try:
                if o["side"] == "long":
                    res = await self.clients[o["exchange"]].close_long(o["symbol"], o["qty"])
                else:
                    res = await self.clients[o["exchange"]].close_short(o["symbol"], o["qty"])
                if res.ok:
                    logger.info("[LIVE ORPHAN RESOLVED] %s %s %s", o["side"], o["exchange"], o["symbol"])
                else:
                    remaining.append(o)
            except Exception:
                remaining.append(o)
        self._orphans = remaining

    def session_summary(self) -> dict:
        return {
            "open_positions": len(self._open),
            "total_opened":   self._total_opened,
            "total_closed":   self._total_closed,
            "total_net_pnl":  self._total_net_pnl,   # matches PaperTrader interface
            "orphans":        len(self._orphans),
        }
