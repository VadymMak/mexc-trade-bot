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
import time
from pathlib import Path

from dotenv import load_dotenv

from .depth_collectors import GatePerpDepth, MexcPerpDepth, SpotDepthPoller
from .depth_store import CarryBookStore
from .depth_symbols import CARRY_BASKET, GATE_SYMBOLS, MEXC_SYMBOLS, chunks

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("carry-depth")

_STATS_INTERVAL = 30.0

# ---- stall escalation (2026-09-14) ---------------------------------------
# This collector feeds `executor.close_carry`'s book curves. When it goes quiet
# the bot cannot price an exit — which is how a dead socket here becomes an
# UNPRICEABLE EXIT there, in exactly the circumstances an exit matters. Until
# now a stale socket was logged as a WARNING and nothing ever escalated, which
# is the same "loud enough to see, never loud enough to act" failure the carry
# bot had. Six sibling collectors already exit for a clean systemd restart on a
# hard stall; this one now matches them.
_STALL_SOFT_SECS = float(os.getenv("CARRY_L2_STALL_SOFT_SECS", 300.0))
_STALL_HARD_SECS = float(os.getenv("CARRY_L2_STALL_HARD_SECS", 900.0))


class L2Stalled(RuntimeError):
    """No perp socket has produced a message for _STALL_HARD_SECS."""


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

    # One websocket per CHUNK of symbols rather than one per venue: in run 1 a
    # single zombie socket took out an entire venue's perp feed for 3.5 days.
    perp = [GatePerpDepth(store, c, tag=str(i))
            for i, c in enumerate(chunks(GATE_SYMBOLS), 1)]
    perp += [MexcPerpDepth(store, c, tag=str(i))
             for i, c in enumerate(chunks(MEXC_SYMBOLS), 1)]
    spot = SpotDepthPoller(store, CARRY_BASKET)
    for p in perp:
        await p.start()
    await spot.start()
    logger.info("[carry/l2] running — %d names, %d perp sockets (%s) + spot(rest)",
                len(CARRY_BASKET), len(perp),
                ", ".join(f"{p.name}:{len(p._symbols)}" for p in perp))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def stats():
        """Per-socket liveness, not just totals.

        Run 1's stats line printed only store totals, which kept climbing on
        spot data while both perp sockets were dead — the log looked healthy.
        Age-since-last-message per socket is the number that would have shown
        it, so it is printed every interval and shouted about when it grows.
        """
        while not stop.is_set():
            await asyncio.sleep(_STATS_INTERVAL)
            now = time.monotonic()
            ages = [(p.name, now - p.last_msg_at if p.last_msg_at else -1.0)
                    for p in perp]
            stale = [f"{n}:{a:.0f}s" for n, a in ages if a < 0 or a > 90]
            logger.info("[carry/l2] snaps=%d rows=%d skipped=%d | spot polls=%d "
                        "err=%d | perp reconnects=%d msgs=%d | oldest socket %.0fs",
                        store.snaps_written, store.rows_written, store.snaps_skipped,
                        spot.polls, spot.errors,
                        sum(p.reconnects for p in perp), sum(p.msgs for p in perp),
                        max((a for _, a in ages), default=0.0))
            if stale:
                logger.warning("[carry/l2] STALE perp sockets: %s", ", ".join(stale))

            # Escalate on the WORK, not on the process being up: the freshest
            # perp socket is the youngest message across all of them. If even
            # that is old, every curve this collector serves is stale and the
            # bot downstream will start failing to price exits.
            freshest = min((a for _, a in ages if a >= 0), default=1e9)
            if freshest > _STALL_HARD_SECS:
                logger.error(
                    "[carry/l2] HARD STALL: no perp message on ANY socket for "
                    "%.0fs (limit %.0fs) — EXITING for a clean systemd restart. "
                    "Downstream effect: close_carry cannot price an exit.",
                    freshest, _STALL_HARD_SECS)
                stop.set()
                raise L2Stalled(f"no perp message for {freshest:.0f}s")
            elif freshest > _STALL_SOFT_SECS:
                # Deliberately a warning and nothing more: each socket already
                # reconnects itself inside `_run`, and there is no verified
                # forced-reconnect entry point to call from here. Inventing one
                # would be a second unverified path in a file whose whole
                # problem was an unverified assumption about socket health.
                logger.error(
                    "[carry/l2] SOFT STALL: freshest perp socket %.0fs old "
                    "(limit %.0fs) — self-reconnect expected; hard stall at %.0fs",
                    freshest, _STALL_SOFT_SECS, _STALL_HARD_SECS)

    st = asyncio.create_task(stats())
    await stop.wait()
    logger.info("[carry/l2] shutting down…")
    st.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await st
    for p in perp:
        await p.stop()
    await spot.stop()
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
