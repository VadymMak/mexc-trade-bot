"""
PaperTrader — listens to SpreadMatrix events, opens/closes paper positions.

Entry condition:
  |zscore| >= ZSCORE_THRESHOLD  AND  spread_pct >= MIN_SPREAD_PCT * 100

Exit conditions:
  |zscore| < 0.5  OR  spread_pct < 0.03 %

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
        if (
            zscore is not None
            and abs(zscore) >= self.settings.ZSCORE_THRESHOLD
            and spread_pct >= self.settings.MIN_SPREAD_PCT * 100
        ):
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

            self._open[key] = _OpenState(
                pos_id=pos_id,
                opened_ms=ts_ms,
                entry_spread=spread_pct,
                entry_zscore=zscore,
                slip_entry=entry_costs["slippage_usdt"],
                fee_entry=entry_costs["fee_usdt"],
            )
            self._total_opened += 1

            # Breakeven: total round-trip cost as % of entry spread
            be = entry_costs["total_cost_usdt"] * 2 / self.sim.deal_size * 100
            logger.info(
                "[OPEN]  %s %s/%s  spread=%.3f%%  z=%+.2f  "
                "size=%.0f USDT  slip=%.4f  fee=%.4f  breakeven=%.3f%%",
                symbol, ex_long, ex_short, spread_pct, zscore,
                self.sim.deal_size,
                entry_costs["slippage_usdt"],
                entry_costs["fee_usdt"],
                be,
            )

    async def _maybe_close(
        self,
        key:       tuple,
        zscore:    Optional[float],
        spread_pct: float,
        ts_ms:     int,
    ) -> None:
        should_exit = (
            (zscore is not None and abs(zscore) < 0.5)
            or spread_pct < 0.03
        )
        if not should_exit:
            return

        state    = self._open.pop(key)
        symbol, ex_long, ex_short = key
        hold_sec = max(0, (ts_ms - state.opened_ms) // 1000)

        result = self.sim.simulate_trade(
            exchange_long=ex_long,
            exchange_short=ex_short,
            entry_spread_pct=state.entry_spread,
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
            "[CLOSE %s] %s %s/%s  "
            "spread %.3f%%→%.3f%%  "
            "gross=%+.4f  slip=%.4f  fee=%.4f  net=%+.4f USDT  "
            "pnl%%=%+.3f%%  hold=%ds",
            verdict, symbol, ex_long, ex_short,
            state.entry_spread, spread_pct,
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
                 "slip_entry", "fee_entry")

    def __init__(
        self,
        pos_id:       int,
        opened_ms:    int,
        entry_spread: float,
        entry_zscore: float,
        slip_entry:   float,
        fee_entry:    float,
    ) -> None:
        self.pos_id       = pos_id
        self.opened_ms    = opened_ms
        self.entry_spread = entry_spread
        self.entry_zscore = entry_zscore
        self.slip_entry   = slip_entry
        self.fee_entry    = fee_entry
