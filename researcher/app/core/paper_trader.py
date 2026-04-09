"""
PaperTrader — listens to SpreadMatrix events, opens/closes paper positions.

Entry condition (either):
  A) z-score mode:  |zscore| >= ZSCORE_THRESHOLD  AND  spread >= MIN_SPREAD_PCT * 100
  B) large spread:  spread >= 5%  (no z-score required — catches new listings)

Exit conditions (first match wins, reason logged):
  1. TAKE_PROFIT   — spread narrowed to <= entry * TAKE_PROFIT_RATIO   (default 50% of entry)
  2. ZSCORE_REVERT — |zscore| < ZSCORE_EXIT  (spread mean-reverted, default z<0.5)
  3. STOP_LOSS     — spread widened to >= entry * STOP_LOSS_RATIO      (default 2×)
  4. TIME_STOP     — held longer than MAX_HOLD_SECONDS                 (default 4h)

Uses TradingSimulator for realistic P&L (fees + slippage + market impact).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..config import Settings
from ..db.neon_db import NeonDB
from .simulator import TradingSimulator

logger = logging.getLogger(__name__)


class PaperTrader:
    def __init__(self, db: NeonDB, settings: Settings) -> None:
        self.db       = db
        self.settings = settings
        self.sim      = TradingSimulator(
            paper_deal_size_usdt=settings.PAPER_DEAL_SIZE_USDT
        )

        # {(symbol, ex_long, ex_short): _OpenState}
        self._open: dict[tuple, _OpenState] = {}

        # Session counters (for log summaries)
        self._total_opened  = 0
        self._total_closed  = 0
        self._total_net_pnl = 0.0

    async def on_spread(self, data: dict) -> None:
        """Called by SpreadMatrix on every aligned spread update."""
        symbol     = data["symbol"]
        ex_long    = data["exchange_long"]
        ex_short   = data["exchange_short"]
        zscore: Optional[float] = data.get("zscore")
        spread_pct = data["spread_pct"]
        ts_ms      = data.get("ts_ms", int(time.time() * 1000))

        key = (symbol, ex_long, ex_short)

        if key in self._open:
            await self._maybe_close(key, zscore, spread_pct, ts_ms)
        else:
            await self._maybe_open(key, symbol, ex_long, ex_short, zscore, spread_pct, ts_ms)

    # ── Session stats (called from report_loop) ───────────────────────────────

    def session_summary(self) -> dict:
        return {
            "open_positions":  len(self._open),
            "total_opened":    self._total_opened,
            "total_closed":    self._total_closed,
            "total_net_pnl":   round(self._total_net_pnl, 4),
            "breakeven_pct":   round(
                self.sim.simulate_trade("binance", "bybit", 0.5, 0.5).breakeven_spread_pct, 4
            ),
        }

    # ── Private ───────────────────────────────────────────────────────────────

    async def _maybe_open(
        self,
        key:       tuple,
        symbol:    str,
        ex_long:   str,
        ex_short:  str,
        zscore:    Optional[float],
        spread_pct: float,
        ts_ms:     int,
    ) -> None:
        # Mode A: classic z-score mean reversion
        zscore_entry = (
            zscore is not None
            and abs(zscore) >= self.settings.ZSCORE_THRESHOLD
            and spread_pct >= self.settings.MIN_SPREAD_PCT * 100
        )
        # Mode B: large-spread entry (new listings, no z-score history needed)
        # spread_pct is stored as decimal percent (0.92 = 92% displayed after ×100)
        # Threshold 0.05 = 5% displayed on screen
        large_spread_entry = spread_pct >= 0.05

        if zscore_entry or large_spread_entry:
            entry_costs = self.sim.simulate_entry(ex_long, ex_short, spread_pct)
            pos_id      = 0

            if self.db._pool:
                pos_id = await self.db.insert_paper_position(
                    symbol=symbol,
                    exchange_long=ex_long,
                    exchange_short=ex_short,
                    entry_spread_pct=spread_pct,
                    entry_zscore=zscore,
                    deal_size_usdt=self.sim.deal_size,
                    slippage_entry_usdt=entry_costs["slippage_usdt"],
                    fee_usdt=entry_costs["fee_usdt"],
                )

            entry_mode = "zscore" if zscore_entry else "large_spread"
            self._open[key] = _OpenState(
                pos_id=pos_id,
                opened_ms=ts_ms,
                entry_spread=spread_pct,
                entry_zscore=zscore,
                slip_entry=entry_costs["slippage_usdt"],
                fee_entry=entry_costs["fee_usdt"],
                entry_mode=entry_mode,
            )
            self._total_opened += 1

            # Breakeven: total round-trip cost as % of entry spread
            be = entry_costs["total_cost_usdt"] * 2 / self.sim.deal_size * 100
            tp_target = spread_pct * self.settings.TAKE_PROFIT_RATIO
            sl_target = spread_pct * self.settings.STOP_LOSS_RATIO
            logger.info(
                "[OPEN %s]  %s %s/%s  spread=%.3f%%  z=%s  "
                "size=%.0f USDT  slip=%.4f  fee=%.4f  breakeven=%.3f%%  "
                "TP@%.3f%%  SL@%.3f%%  timeout=%dh",
                entry_mode.upper(), symbol, ex_long, ex_short, spread_pct,
                f"{zscore:+.2f}" if zscore is not None else "n/a",
                self.sim.deal_size,
                entry_costs["slippage_usdt"],
                entry_costs["fee_usdt"],
                be,
                tp_target,
                sl_target,
                self.settings.MAX_HOLD_SECONDS // 3600,
            )

    async def _maybe_close(
        self,
        key:       tuple,
        zscore:    Optional[float],
        spread_pct: float,
        ts_ms:     int,
    ) -> None:
        state    = self._open[key]
        hold_sec = max(0, (ts_ms - state.opened_ms) // 1000)
        entry    = state.entry_spread

        # ── Exit condition checks (first match wins) ──────────────────────
        reason: Optional[str] = None

        # 1. Take-profit: spread narrowed to ≤ entry × TAKE_PROFIT_RATIO
        tp_threshold = entry * self.settings.TAKE_PROFIT_RATIO
        if spread_pct <= tp_threshold:
            reason = "TAKE_PROFIT"

        # 2. Z-score revert: spread returned to mean (only if z-score available)
        elif zscore is not None and abs(zscore) < self.settings.ZSCORE_EXIT:
            reason = "ZSCORE_REVERT"

        # 3. Stop-loss: spread grew too wide (position moving against us)
        elif spread_pct >= entry * self.settings.STOP_LOSS_RATIO:
            reason = "STOP_LOSS"

        # 4. Time stop: max hold exceeded
        elif hold_sec >= self.settings.MAX_HOLD_SECONDS:
            reason = "TIME_STOP"

        if reason is None:
            return

        # ── Execute close ─────────────────────────────────────────────────
        self._open.pop(key)
        symbol, ex_long, ex_short = key

        result = self.sim.simulate_trade(
            exchange_long=ex_long,
            exchange_short=ex_short,
            entry_spread_pct=entry,
            exit_spread_pct=spread_pct,
        )

        if self.db._pool:
            await self.db.close_paper_position(
                pos_id=state.pos_id,
                exit_spread_pct=spread_pct,
                exit_zscore=zscore,
                slippage_exit_usdt=result.slippage_exit_usdt,
                gross_pnl_usdt=result.gross_pnl_usdt,
                net_pnl_usdt=result.net_pnl_usdt,
                hold_seconds=hold_sec,
            )
            await self.db.upsert_pair_stats(symbol, ex_long, ex_short)

        self._total_closed  += 1
        self._total_net_pnl += result.net_pnl_usdt
        verdict = "WIN " if result.net_pnl_usdt > 0 else "LOSS"

        logger.info(
            "[CLOSE %s | %s] %s %s/%s  "
            "spread %.3f%%→%.3f%%  (entry_mode=%s)  "
            "gross=%+.4f  slip=%.4f  fee=%.4f  net=%+.4f USDT  "
            "pnl%%=%+.3f%%  hold=%ds",
            verdict, reason,
            symbol, ex_long, ex_short,
            entry, spread_pct,
            state.entry_mode,
            result.gross_pnl_usdt,
            result.slippage_entry_usdt + result.slippage_exit_usdt,
            result.fee_usdt,
            result.net_pnl_usdt,
            result.net_pnl_pct,
            hold_sec,
        )


class _OpenState:
    """Lightweight container for an open position's state."""
    __slots__ = ("pos_id", "opened_ms", "entry_spread", "entry_zscore",
                 "slip_entry", "fee_entry", "entry_mode")

    def __init__(
        self,
        pos_id:       int,
        opened_ms:    int,
        entry_spread: float,
        entry_zscore: Optional[float],
        slip_entry:   float,
        fee_entry:    float,
        entry_mode:   str = "zscore",
    ) -> None:
        self.pos_id       = pos_id
        self.opened_ms    = opened_ms
        self.entry_spread = entry_spread
        self.entry_zscore = entry_zscore
        self.slip_entry   = slip_entry
        self.fee_entry    = fee_entry
        self.entry_mode   = entry_mode
