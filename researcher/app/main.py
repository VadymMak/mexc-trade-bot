"""
Researcher service — entry point.

Run from the researcher/ directory:
    python -m app.main
"""
from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .collectors.binance_collector import BinanceCollector
from .collectors.bybit_collector import BybitCollector
from .collectors.gate_collector import GateCollector
from .core.spread_matrix import SpreadMatrix
from .core.paper_trader import PaperTrader
from .db.neon_db import NeonDB


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("researcher")

    log.info("Starting researcher. Symbols: %s", settings.symbols_list)

    # DB
    db = NeonDB(settings.NEON_DATABASE_URL)
    if settings.NEON_DATABASE_URL:
        await db.connect()
        log.info("Neon DB connected")
    else:
        log.warning("No NEON_DATABASE_URL — running without DB (dry run)")

    # Core
    matrix = SpreadMatrix(max_lag_ms=settings.MAX_SPREAD_LAG_MS)
    trader = PaperTrader(db=db, settings=settings)
    matrix.add_callback(trader.on_spread)

    # Push spreads to trading bot every 5s
    matrix.set_push_url(
        url=f"{settings.TRADING_BOT_URL}/api/arbitrage/internal/spread-update",
        interval_s=5,
    )

    # Collectors
    collectors = [
        BinanceCollector(),
        BybitCollector(),
        GateCollector(),
    ]
    for c in collectors:
        c.set_callback(matrix.on_price)

    # Connect all (gather, don't fail if one exchange is down)
    connect_tasks = [c.connect(settings.symbols_list) for c in collectors]
    results = await asyncio.gather(*connect_tasks, return_exceptions=True)
    for c, r in zip(collectors, results):
        if isinstance(r, Exception):
            log.error("%s failed to connect: %r", c.name, r)
        else:
            log.info("%s connected OK", c.name)

    # Stats report every 60s
    async def report_loop() -> None:
        while True:
            await asyncio.sleep(60)
            spreads = matrix.get_all_spreads()
            active = len(trader._open)
            log.info(
                "Spreads tracked: %d | Open paper positions: %d",
                len(spreads), active,
            )

    await asyncio.gather(report_loop(), matrix.push_loop())


if __name__ == "__main__":
    asyncio.run(main())
