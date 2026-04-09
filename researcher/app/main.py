"""
Researcher service — entry point.

Run from the researcher/ directory:
    python -m app.main

Wire-up order (to be implemented):
    collectors → spread_matrix.on_price()
              → paper_trader.on_spread()
              → neon_db.insert_spread_tick()
              → promoter.evaluate() → POST /api/arbitrage/queue
"""
from __future__ import annotations

import asyncio
import logging

from .config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("researcher")


async def main() -> None:
    logger.info("Researcher starting…")
    logger.info("Symbols: %s", settings.SYMBOLS)
    logger.info("Min spread: %.4f%%", settings.MIN_SPREAD_PCT * 100)

    # TODO: wire collectors → spread_matrix → paper_trader
    #
    # from .collectors.binance_collector import BinanceCollector
    # from .collectors.bybit_collector import BybitCollector
    # from .collectors.gate_collector import GateCollector
    # from .core.spread_matrix import SpreadMatrix
    # from .db.neon_db import close_pool
    #
    # matrix = SpreadMatrix()
    #
    # collectors = [BinanceCollector(), BybitCollector(), GateCollector()]
    # for c in collectors:
    #     c.set_callback(matrix.on_price)
    #     await c.connect(settings.SYMBOLS)
    #
    # try:
    #     await asyncio.Event().wait()   # run forever
    # finally:
    #     for c in collectors:
    #         await c.disconnect()
    #     await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
