"""Ёрш raw-tape collector — entry point.

    cd /home/vadym/mexc-trade-bot/researcher && .venv/bin/python -m app.ersh.main

DATA COLLECTION ONLY — no orders, no trading, no interaction with the paper or
live traders. Writes two additive tables: tape_prints and book_ticker.

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

from .gate_tape import GateTapeCollector
from .mexc_tape import MexcTapeCollector
from .store import TapeStore
from .symbols import (GATE_SYMBOLS, MEXC_SYMBOLS,
                       CARRY_TAPE_GATE, CARRY_TAPE_MEXC)

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ersh")

_STATS_INTERVAL = 30.0


async def _stats_loop(store: TapeStore, stop: asyncio.Event) -> None:
    prev_p = prev_q = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=_STATS_INTERVAL)
            return
        except asyncio.TimeoutError:
            pass
        p, q = store.prints_written, store.quotes_written
        logger.info(
            "[ersh] prints=%d (+%d, %.1f/s)  quotes=%d (+%d, %.1f/s)",
            p, p - prev_p, (p - prev_p) / _STATS_INTERVAL,
            q, q - prev_q, (q - prev_q) / _STATS_INTERVAL,
        )
        prev_p, prev_q = p, q


async def run() -> None:
    dsn = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("NEON_DATABASE_URL is not set in researcher/.env")

    store = TapeStore(dsn)
    await store.connect()

    # ERSH_SYMBOL_SET selects which universe this process collects.
    # Default "ersh" is the original behaviour, unchanged — a second unit runs
    # with "carry" so the two streams never share a process or a restart.
    _set = os.getenv("ERSH_SYMBOL_SET", "ersh").lower()
    if _set == "carry":
        mexc_syms, gate_syms = CARRY_TAPE_MEXC, CARRY_TAPE_GATE
    else:
        mexc_syms, gate_syms = MEXC_SYMBOLS, GATE_SYMBOLS
    logger.info("[ersh] symbol set = %s", _set)

    mexc = MexcTapeCollector(store, mexc_syms)
    gate = GateTapeCollector(store, gate_syms)

    logger.info("[ersh] candidates: mexc=%d gate=%d", len(mexc_syms), len(gate_syms))
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
        logger.info("[ersh] shutting down…")
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
