"""
PairPromoter — hourly job that checks pair_stats for promotion candidates.

Criteria (configurable via Settings):
  score            >= PROMOTE_THRESHOLD * 100  (default 75)
  total_trades     >= MIN_TRADES_TO_PROMOTE     (default 50)
  win_rate         >= 58 %
  promoted         = FALSE

On match: POST to trading-bot /api/arbitrage/internal/queue-suggest,
          then mark pair as promoted to avoid double-posting.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from ..config import Settings
from ..db.neon_db import NeonDB

logger = logging.getLogger(__name__)


class PairPromoter:
    def __init__(
        self,
        db:       NeonDB,
        settings: Settings,
        interval_s: float = 3600.0,  # 1 hour
    ) -> None:
        self.db         = db
        self.settings   = settings
        self._interval  = interval_s

    async def run(self) -> None:
        """Periodic promotion loop — runs forever."""
        logger.info("[Promoter] Started (interval=%.0fs)", self._interval)
        while True:
            await asyncio.sleep(self._interval)
            if self.db._pool:
                await self._promote_round()
            else:
                logger.debug("[Promoter] No DB pool — skipping")

    async def _promote_round(self) -> None:
        candidates = await self.db.get_queue_candidates(
            min_score=self.settings.PROMOTE_THRESHOLD * 100,
            min_trades=self.settings.MIN_TRADES_TO_PROMOTE,
            min_win_rate=0.58,
        )
        if not candidates:
            logger.info("[Promoter] No promotion candidates this hour")
            return

        logger.info("[Promoter] %d candidates found — posting to trading bot", len(candidates))
        async with aiohttp.ClientSession() as session:
            for c in candidates:
                await self._suggest_one(session, c)

    async def _suggest_one(
        self,
        session:   aiohttp.ClientSession,
        candidate: dict,
    ) -> None:
        url = f"{self.settings.TRADING_BOT_URL}/api/arbitrage/internal/queue-suggest"
        payload = {
            "symbol":        candidate["symbol"],
            "exchange_long": candidate["exchange_long"],
            "exchange_short": candidate["exchange_short"],
            "score":         float(candidate.get("score") or 0),
            "total_trades":  int(candidate.get("total_trades") or 0),
            "win_rate":      (
                int(candidate.get("win_trades") or 0)
                / max(int(candidate.get("total_trades") or 1), 1)
            ),
            "sharpe":        float(candidate.get("sharpe") or 0),
            "max_drawdown_pct": float(candidate.get("max_drawdown_pct") or 0),
            "total_net_pnl": float(candidate.get("total_net_pnl") or 0),
        }
        try:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status < 300:
                    await self.db.mark_promoted(
                        candidate["symbol"],
                        candidate["exchange_long"],
                        candidate["exchange_short"],
                    )
                    logger.info(
                        "[Promoter] ✓ Promoted %s %s/%s  score=%.1f  win_rate=%.1f%%",
                        candidate["symbol"],
                        candidate["exchange_long"],
                        candidate["exchange_short"],
                        payload["score"],
                        payload["win_rate"] * 100,
                    )
                else:
                    body = await resp.text()
                    logger.warning(
                        "[Promoter] POST failed HTTP %d for %s: %s",
                        resp.status, candidate["symbol"], body[:200],
                    )
        except Exception as exc:
            logger.warning(
                "[Promoter] Error posting %s %s/%s: %r",
                candidate["symbol"],
                candidate["exchange_long"],
                candidate["exchange_short"],
                exc,
            )
