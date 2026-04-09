from __future__ import annotations

import logging

from ..config import Settings
from ..db.neon_db import NeonDB

logger = logging.getLogger(__name__)


class PaperTrader:
    """
    Listens to SpreadMatrix events.
    Opens paper position when zscore > threshold.
    Closes when zscore < exit_threshold OR hold > max_hold_minutes.
    """

    FEE_RATE = 0.0002  # 0.02% per leg × 4 legs = 0.08% round trip

    def __init__(self, db: NeonDB, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        # open paper positions: key=(symbol, ex_long, ex_short), value=position_id
        self._open: dict[tuple, int] = {}

    async def on_spread(self, data: dict) -> None:
        """Called by SpreadMatrix on every aligned spread update."""
        key = (data["symbol"], data["exchange_long"], data["exchange_short"])
        zscore = data.get("zscore")
        spread_pct = data["spread_pct"]

        if key in self._open:
            # Check exit conditions
            pos_id = self._open[key]
            should_exit = (
                (zscore is not None and abs(zscore) < 0.5)
                or spread_pct < 0.03
            )
            if should_exit:
                await self.db.close_paper_position(pos_id, spread_pct)
                await self.db.upsert_pair_stats(*key)
                del self._open[key]
                logger.info(
                    "[CLOSE] %s %s/%s spread=%.3f%%",
                    key[0], key[1], key[2], spread_pct,
                )
        else:
            # Check entry conditions
            if (
                zscore is not None
                and abs(zscore) >= self.settings.ZSCORE_THRESHOLD
                and spread_pct >= self.settings.MIN_SPREAD_PCT * 100
            ):
                fee = self.settings.PAPER_DEAL_SIZE_USDT * self.FEE_RATE * 4
                pos_id = await self.db.insert_paper_position({
                    "symbol": data["symbol"],
                    "exchange_long": data["exchange_long"],
                    "exchange_short": data["exchange_short"],
                    "spread_pct": spread_pct,
                    "fee_usdt": fee,
                })
                self._open[key] = pos_id
                logger.info(
                    "[OPEN]  %s %s/%s spread=%.3f%% z=%.2f",
                    key[0], key[1], key[2], spread_pct, zscore,
                )
