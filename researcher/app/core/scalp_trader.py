"""
ScalpPaperTrader — single-exchange directional scalping simulator.

Strategy: detect active MM robots (mm_repeat_score) + directional flow (buy_pressure)
→ enter in direction of dominant flow → exit on small TP or SL.

Entry conditions (ALL required):
  mm_repeat_score >= MM_MIN_SCORE  (0.5) — MM robot active, market has structure
  buy_pressure    >= BP_LONG       (0.65) → LONG  (buying dominant)
  buy_pressure    <= BP_SHORT      (0.35) → SHORT (selling dominant)
  trade_velocity  >= MIN_VELOCITY  (10)   — market not dead

Exit (first match wins):
  TAKE_PROFIT  — price moved TP_PCT (0.15%) in our direction
  STOP_LOSS    — price moved SL_PCT (0.20%) against us
  TIMEOUT      — held > MAX_HOLD_SEC (300s = 5 min)

Fees: MEXC taker 0.02% per side → 0.04% round-trip
Breakeven: ~0.05% (vs arb 0.26%) — much tighter market possible.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..db.neon_db import NeonDB

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
MM_MIN_SCORE   = 0.50   # mm_repeat_score: fraction of same-size trades required
BP_LONG        = 0.65   # buy_pressure threshold for LONG entry
BP_SHORT       = 0.35   # buy_pressure threshold for SHORT entry
MIN_VELOCITY   = 10     # minimum trades/min to consider entry
TP_PCT         = 0.15   # take-profit: 0.15% price move in direction
SL_PCT         = 0.20   # stop-loss:   0.20% price move against
MAX_HOLD_SEC   = 300    # 5 min max hold
DEAL_SIZE_USDT = 10.0   # paper position size
FEE_PCT        = 0.02   # MEXC taker fee per side (%)
SL_COOLDOWN    = 120    # seconds cooldown after stop-loss on same symbol

EXCHANGE = "mexc"       # single exchange for scalping


class ScalpPaperTrader:
    """
    Listens to SpreadMatrix ticks, extracts MEXC price, runs scalp simulation.
    Runs alongside PaperTrader — same data feed, different strategy.
    """

    def __init__(self, db: NeonDB) -> None:
        self.db = db

        # symbol → _ScalpState
        self._open: dict[str, _ScalpState] = {}

        # symbol → cooldown_until_ms
        self._sl_cooldown: dict[str, int] = {}

        # counters
        self._total_opened  = 0
        self._total_closed  = 0
        self._total_net_pnl = 0.0

    async def on_spread(self, data: dict) -> None:
        """Called by SpreadMatrix on every tick. Extracts MEXC price."""
        symbol       = data["symbol"]
        ex_long      = data["exchange_long"]
        ex_short     = data["exchange_short"]

        # Extract MEXC price from whichever side it is
        if ex_long == EXCHANGE:
            mexc_price = data["price_long"]
        elif ex_short == EXCHANGE:
            mexc_price = data["price_short"]
        else:
            return  # no MEXC in this tick

        ts_ms           = data.get("ts_ms", int(time.time() * 1000))
        mm_repeat_score = data.get("mm_repeat_score")
        buy_pressure    = data.get("buy_pressure")
        trade_velocity  = data.get("trade_velocity")
        book_imbalance  = data.get("book_imbalance")
        spread_cv       = data.get("spread_cv")

        if symbol in self._open:
            await self._maybe_close(symbol, mexc_price, ts_ms)
        else:
            await self._maybe_open(
                symbol, mexc_price, ts_ms,
                mm_repeat_score=mm_repeat_score,
                buy_pressure=buy_pressure,
                trade_velocity=trade_velocity,
                book_imbalance=book_imbalance,
                spread_cv=spread_cv,
            )

    def session_summary(self) -> dict:
        return {
            "open_scalp":      len(self._open),
            "total_opened":    self._total_opened,
            "total_closed":    self._total_closed,
            "total_net_pnl":   round(self._total_net_pnl, 4),
        }

    # ── Private ───────────────────────────────────────────────────────────────

    async def _maybe_open(
        self,
        symbol:          str,
        price:           float,
        ts_ms:           int,
        mm_repeat_score: Optional[float],
        buy_pressure:    Optional[float],
        trade_velocity:  Optional[float],
        book_imbalance:  Optional[float],
        spread_cv:       Optional[float],
    ) -> None:
        # Need flow data to decide
        if mm_repeat_score is None or buy_pressure is None or trade_velocity is None:
            return

        # MM robot must be detectable
        if mm_repeat_score < MM_MIN_SCORE:
            return

        # Market must be active
        if trade_velocity < MIN_VELOCITY:
            return

        # Check cooldown
        if ts_ms < self._sl_cooldown.get(symbol, 0):
            return

        # Direction from buy pressure
        if buy_pressure >= BP_LONG:
            direction = "LONG"
        elif buy_pressure <= BP_SHORT:
            direction = "SHORT"
        else:
            return  # neutral — no signal

        # Reserve slot before async DB call
        self._open[symbol] = _ScalpState(
            pos_id=0,
            opened_ms=ts_ms,
            entry_price=price,
            direction=direction,
        )
        self._total_opened += 1

        tp_price = (
            price * (1 + TP_PCT / 100) if direction == "LONG"
            else price * (1 - TP_PCT / 100)
        )
        sl_price = (
            price * (1 - SL_PCT / 100) if direction == "LONG"
            else price * (1 + SL_PCT / 100)
        )

        pos_id = 0
        if self.db._pool:
            pos_id = await self.db.insert_scalp_position(
                symbol=symbol,
                exchange=EXCHANGE,
                direction=direction,
                entry_price=price,
                deal_size_usdt=DEAL_SIZE_USDT,
                mm_repeat_score=mm_repeat_score,
                buy_pressure=buy_pressure,
                trade_velocity=trade_velocity,
                book_imbalance=book_imbalance,
                spread_cv=spread_cv,
            )
            self._open[symbol].pos_id = pos_id

        logger.info(
            "[SCALP OPEN %s]  %s @%.6f  mm=%.2f  bp=%.2f  vel=%.0f  "
            "TP@%.6f  SL@%.6f  hold≤%ds",
            direction, symbol, price,
            mm_repeat_score, buy_pressure, trade_velocity,
            tp_price, sl_price, MAX_HOLD_SEC,
        )

    async def _maybe_close(
        self,
        symbol: str,
        price:  float,
        ts_ms:  int,
    ) -> None:
        state    = self._open[symbol]
        hold_sec = max(0, (ts_ms - state.opened_ms) // 1000)
        entry    = state.entry_price

        reason: Optional[str] = None

        if state.direction == "LONG":
            if price >= entry * (1 + TP_PCT / 100):
                reason = "TAKE_PROFIT"
            elif price <= entry * (1 - SL_PCT / 100):
                reason = "STOP_LOSS"
        else:  # SHORT
            if price <= entry * (1 - TP_PCT / 100):
                reason = "TAKE_PROFIT"
            elif price >= entry * (1 + SL_PCT / 100):
                reason = "STOP_LOSS"

        if reason is None and hold_sec >= MAX_HOLD_SEC:
            reason = "TIMEOUT"

        if reason is None:
            return

        del self._open[symbol]

        if reason == "STOP_LOSS":
            self._sl_cooldown[symbol] = ts_ms + SL_COOLDOWN * 1000

        # P&L calculation
        if state.direction == "LONG":
            gross_pnl = (price - entry) / entry * DEAL_SIZE_USDT
        else:
            gross_pnl = (entry - price) / entry * DEAL_SIZE_USDT

        fee_cost  = DEAL_SIZE_USDT * FEE_PCT / 100 * 2  # entry + exit
        net_pnl   = gross_pnl - fee_cost
        pnl_pct   = net_pnl / DEAL_SIZE_USDT * 100

        self._total_closed  += 1
        self._total_net_pnl += net_pnl
        verdict = "WIN " if net_pnl > 0 else "LOSS"

        if self.db._pool:
            await self.db.close_scalp_position(
                pos_id=state.pos_id,
                exit_price=price,
                hold_seconds=hold_sec,
                gross_pnl_usdt=gross_pnl,
                net_pnl_usdt=net_pnl,
                exit_reason=reason,
            )

        logger.info(
            "[SCALP CLOSE %s | %s] %s @%.6f→%.6f  "
            "gross=%+.4f  fee=%.4f  net=%+.4f USDT  pnl%%=%+.3f%%  hold=%ds",
            verdict, reason, symbol, entry, price,
            gross_pnl, fee_cost, net_pnl, pnl_pct, hold_sec,
        )


class _ScalpState:
    __slots__ = ("pos_id", "opened_ms", "entry_price", "direction")

    def __init__(self, pos_id: int, opened_ms: int,
                 entry_price: float, direction: str) -> None:
        self.pos_id      = pos_id
        self.opened_ms   = opened_ms
        self.entry_price = entry_price
        self.direction   = direction
