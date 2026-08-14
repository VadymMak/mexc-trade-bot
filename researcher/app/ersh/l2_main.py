"""Ёрш L2 touch-depth collector — entry point.

    cd /home/vadym/mexc-trade-bot/researcher && .venv/bin/python -m app.ersh.l2_main

DATA COLLECTION ONLY — no orders, no trading, no interaction with the paper or
live traders. Writes one additive table: ersh_book_l2. Runs as its own service
(mexc-ersh-l2) so the tape collector (mexc-ersh-tape) is never restarted: the
sim needs tape_prints aligned with these snapshots, so that stream must not gap.

Only the five locked-1-tick candidates are collected — see l2_symbols.py for why
the rest of the ёрш candidate set was dropped.

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

from .l2_gate import GateL2Collector
from .l2_mexc import MexcL2Collector
from .l2_store import L2BookStore
from .l2_symbols import GATE_L2_SYMBOLS, MEXC_L2_SYMBOLS

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ersh-l2")

_STATS_INTERVAL = 30.0


async def _load_multipliers() -> dict:
    """Contract multipliers for the 5 candidates, via the existing spec cache.

    A missing spec is not fatal — l2_store writes size_usd NULL rather than a
    wrong-unit number.
    """
    from ..core.contract_specs import ContractSpecs

    specs = ContractSpecs()
    try:
        await specs.load()
    except Exception as exc:
        logger.warning("[ersh/l2] contract spec load failed: %r — size_usd will be NULL", exc)
        return {}

    out: dict[tuple[str, str], float] = {}
    for ex, syms in (("mexc", MEXC_L2_SYMBOLS), ("gate", GATE_L2_SYMBOLS)):
        for s in syms:
            m = specs.get(ex, s)
            if m:
                out[(ex, s.upper())] = m
            else:
                logger.warning("[ersh/l2] no contract spec for %s %s — size_usd NULL", ex, s)
    logger.info("[ersh/l2] multipliers: %s",
                ", ".join(f"{ex}:{sym}={m:g}" for (ex, sym), m in sorted(out.items())))
    return out


async def _stats_loop(store: L2BookStore, stop: asyncio.Event) -> None:
    prev_s = prev_r = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=_STATS_INTERVAL)
            return
        except asyncio.TimeoutError:
            pass
        s, r = store.snaps_written, store.rows_written
        logger.info(
            "[ersh/l2] snapshots=%d (+%d, %.1f/s)  rows=%d (+%d, %.1f/s)  dedup-skipped=%d",
            s, s - prev_s, (s - prev_s) / _STATS_INTERVAL,
            r, r - prev_r, (r - prev_r) / _STATS_INTERVAL,
            store.snaps_skipped,
        )
        prev_s, prev_r = s, r


async def run() -> None:
    dsn = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("NEON_DATABASE_URL is not set in researcher/.env")

    store = L2BookStore(dsn)
    store.set_multipliers(await _load_multipliers())
    await store.connect()

    mexc = MexcL2Collector(store, MEXC_L2_SYMBOLS)
    gate = GateL2Collector(store, GATE_L2_SYMBOLS)

    logger.info("[ersh/l2] locked-1-tick candidates: mexc=%s gate=%s",
                MEXC_L2_SYMBOLS, GATE_L2_SYMBOLS)
    await mexc.start()
    await gate.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    stats = asyncio.create_task(_stats_loop(store, stop))
    try:
        await stop.wait()
    finally:
        logger.info("[ersh/l2] shutting down…")
        stats.cancel()
        await mexc.stop()
        await gate.stop()
        await store.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
