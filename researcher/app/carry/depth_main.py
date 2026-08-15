"""Carry depth collector — entry point.

    cd /home/vadym/mexc-trade-bot/researcher && .venv/bin/python -m app.carry.depth_main

DATA COLLECTION ONLY — no orders, no trading. Writes ONE additive table:
carry_book_l2. Runs as its own service (mexc-carry-depth) so that neither the
funding collector (app.carry.main -> funding_basis_snapshots) nor either ёрш
collector is ever restarted or otherwise disturbed.

Purpose: funding_basis_snapshots has perp_depth5_usd/spot_depth5_usd 100% NULL,
so the Phase 1 carry screen is entirely size-blind. This measures the real
capacity of the starter basket — how much we can actually buy on spot and short
on perp within acceptable slippage.

DB: NEON_DATABASE_URL from researcher/.env (historical name — it points at the
local trading_bot PostgreSQL).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv

from .depth_collectors import GatePerpDepth, MexcPerpDepth, SpotDepthPoller
from .depth_store import CarryBookStore
from .depth_symbols import CARRY_BASKET, GATE_SYMBOLS, MEXC_SYMBOLS

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("carry-depth")

_STATS_INTERVAL = 30.0


async def _load_multipliers() -> dict:
    """Contract multipliers for the perp legs, via the existing spec cache.

    A missing spec is not fatal — depth_store writes size_usd NULL rather than a
    wrong-unit number, because a wrong capacity figure is worse than none.
    """
    from ..core.contract_specs import ContractSpecs

    specs = ContractSpecs()
    try:
        await specs.load()
    except Exception as exc:
        logger.warning("[carry/l2] contract spec load failed: %r — size_usd will be NULL", exc)
        return {}
    out: dict[tuple[str, str], float] = {}
    for ex, sym in CARRY_BASKET:
        m = specs.get(ex, sym)
        if m is None:
            logger.warning("[carry/l2] no contract spec for %s/%s — size_usd NULL", ex, sym)
        else:
            out[(ex, sym.upper())] = m
    logger.info("[carry/l2] multipliers: %s", {f"{k[0]}/{k[1]}": v for k, v in out.items()})
    return out


async def main() -> None:
    dsn = os.getenv("NEON_DATABASE_URL", "")
    if not dsn:
        raise SystemExit("NEON_DATABASE_URL not set")

    store = CarryBookStore(dsn)
    await store.connect()
    store.set_multipliers(await _load_multipliers())

    gate = GatePerpDepth(store, GATE_SYMBOLS)
    mexc = MexcPerpDepth(store, MEXC_SYMBOLS)
    spot = SpotDepthPoller(store, CARRY_BASKET)
    await gate.start()
    await mexc.start()
    await spot.start()
    logger.info("[carry/l2] running — %d names, perp(ws) + spot(rest)", len(CARRY_BASKET))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def stats():
        while not stop.is_set():
            await asyncio.sleep(_STATS_INTERVAL)
            logger.info("[carry/l2] snaps=%d rows=%d skipped=%d",
                        store.snaps_written, store.rows_written, store.snaps_skipped)

    st = asyncio.create_task(stats())
    await stop.wait()
    logger.info("[carry/l2] shutting down…")
    st.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await st
    await gate.stop()
    await mexc.stop()
    await spot.stop()
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
