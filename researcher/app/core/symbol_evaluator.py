"""
SymbolEvaluator — automatic symbol lifecycle management.

States:
  TESTING     → collecting data for at least TEST_DAYS days AND TEST_MIN_TRADES trades
  APPROVED    → meets performance criteria → allowed in paper (and eventually live) trading
  BLACKLISTED → failed evaluation → skipped for BLACKLIST_RETEST_DAYS, then re-enters TESTING

Evaluation criteria (configurable via settings):
  APPROVED if:
    tp_rate    >= APPROVE_TP_RATE    (default 50%)
    net_pnl    >  0
    trades     >= TEST_MIN_TRADES    (default 30)
    days       >= TEST_DAYS          (default 7)

  BLACKLISTED if:
    tp_rate    <  BLACKLIST_TP_RATE  (default 30%)   OR
    net_pnl    <= BLACKLIST_MAX_LOSS (default -$1)
    AND still meets min trades/days thresholds

  Otherwise: stay in TESTING (more data needed).

Called after every trade close by PaperTrader (async, fire-and-forget).
Also runs a full sweep at startup to catch any unresolved states.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.neon_db import NeonDB
    from ..config import Settings

logger = logging.getLogger(__name__)

# ── Thresholds (can be moved to Settings later) ─────────────────────────────
TEST_DAYS              = 7       # minimum calendar days before verdict
TEST_MIN_TRADES        = 30      # minimum trades before verdict
APPROVE_TP_RATE        = 0.50    # TP rate >= 50% → APPROVED
BLACKLIST_TP_RATE      = 0.30    # TP rate <  30% → candidate for BLACKLISTED
BLACKLIST_MAX_LOSS     = -1.0    # net PnL <= -$1 → BLACKLISTED regardless of TP rate
BLACKLIST_RETEST_DAYS  = 30      # days before a blacklisted symbol is retested


class SymbolEvaluator:
    """
    Evaluates symbol performance and updates symbol_states table.

    Usage:
        evaluator = SymbolEvaluator(db)
        await evaluator.on_trade_closed(symbol)   # call after each close
        await evaluator.run_full_sweep()           # call once at startup
    """

    def __init__(self, db: "NeonDB") -> None:
        self._db = db

    async def on_trade_closed(self, symbol: str) -> None:
        """
        Called after a paper trade closes.
        Ensures symbol is tracked, then checks if evaluation criteria are met.
        """
        await self._db.ensure_symbol_testing(symbol)
        await self._evaluate_symbol(symbol)

    async def run_full_sweep(self) -> None:
        """
        Evaluate all TESTING symbols at startup.
        Handles any symbols that accumulated enough trades while evaluator was offline.
        """
        assert self._db._pool
        rows = await self._db._pool.fetch(
            "SELECT symbol FROM symbol_states WHERE state = 'TESTING'"
        )
        for row in rows:
            await self._evaluate_symbol(row["symbol"])
        logger.info("[Evaluator] Full sweep done — %d TESTING symbols checked", len(rows))

    async def _evaluate_symbol(self, symbol: str) -> None:
        assert self._db._pool

        # ── Fetch rolling 7-day clean stats ──────────────────────────────────
        # "Clean" = gate/mexc only + ZSCORE_REVERT hold >= 120s
        row = await self._db._pool.fetchrow(
            """
            SELECT
                COUNT(*)                                                  AS total,
                SUM(CASE WHEN exit_reason='TAKE_PROFIT' THEN 1 ELSE 0 END) AS tp_count,
                SUM(net_pnl_usdt)                                         AS net_pnl,
                MIN(opened_at)                                            AS first_trade,
                MAX(opened_at)                                            AS last_trade
            FROM paper_positions
            WHERE symbol = $1
              AND status  = 'closed'
              AND opened_at >= NOW() - INTERVAL '7 days'
              -- Clean filter: no phantom exchanges
              AND exchange_long  NOT IN ('binance', 'bybit')
              AND exchange_short NOT IN ('binance', 'bybit')
              -- Clean filter: no early ZR noise
              AND NOT (exit_reason = 'ZSCORE_REVERT' AND hold_seconds < 120)
            """,
            symbol,
        )

        if row is None:
            return

        total       = int(row["total"] or 0)
        tp_count    = int(row["tp_count"] or 0)
        net_pnl     = float(row["net_pnl"] or 0.0)
        first_trade: datetime | None = row["first_trade"]
        last_trade:  datetime | None = row["last_trade"]

        tp_rate = tp_count / total if total > 0 else 0.0

        # ── Check if we have enough data for a verdict ───────────────────────
        if total < TEST_MIN_TRADES:
            logger.debug(
                "[Evaluator] %s: only %d trades (need %d) — staying TESTING",
                symbol, total, TEST_MIN_TRADES,
            )
            return

        if first_trade is None or last_trade is None:
            return

        # Ensure first_trade is timezone-aware
        if first_trade.tzinfo is None:
            first_trade = first_trade.replace(tzinfo=timezone.utc)

        days_observed = (datetime.now(timezone.utc) - first_trade).total_seconds() / 86_400
        if days_observed < TEST_DAYS:
            logger.debug(
                "[Evaluator] %s: only %.1f days (need %d) — staying TESTING",
                symbol, days_observed, TEST_DAYS,
            )
            return

        # ── Verdict ──────────────────────────────────────────────────────────
        if tp_rate >= APPROVE_TP_RATE and net_pnl > 0:
            state  = "APPROVED"
            reason = (f"tp_rate={tp_rate:.1%} net_pnl=${net_pnl:.4f} "
                      f"trades={total} days={days_observed:.1f}")
            logger.info("[Evaluator] ✅ APPROVED %s — %s", symbol, reason)

        elif tp_rate < BLACKLIST_TP_RATE or net_pnl <= BLACKLIST_MAX_LOSS:
            state  = "BLACKLISTED"
            reason = (f"tp_rate={tp_rate:.1%} net_pnl=${net_pnl:.4f} "
                      f"trades={total} days={days_observed:.1f} "
                      f"retest_in={BLACKLIST_RETEST_DAYS}d")
            logger.warning("[Evaluator] ❌ BLACKLISTED %s — %s", symbol, reason)

        else:
            # Marginal: 30-50% TP and still positive — need more data
            logger.debug(
                "[Evaluator] %s: marginal (tp=%.1f%% pnl=$%.4f) — staying TESTING",
                symbol, tp_rate * 100, net_pnl,
            )
            return

        await self._db.update_symbol_state(
            symbol       = symbol,
            state        = state,
            total_trades = total,
            tp_rate      = tp_rate,
            net_pnl      = net_pnl,
            reason       = reason,
            retest_days  = BLACKLIST_RETEST_DAYS,
        )
