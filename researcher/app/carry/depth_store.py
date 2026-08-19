"""Batched writer for carry_book_l2 — the carry capacity table.

ADDITIVE: one new table, self-healing CREATE TABLE IF NOT EXISTS. Touches no
existing table and no existing collector. funding_basis_snapshots collection is
completely unaffected.

Rows are one per book LEVEL, so a snapshot is 2 * LEVELS rows sharing one `ts`;
recover a snapshot with GROUP BY exchange, symbol, market, ts.

`size` is the venue's native unit:
    perp -> CONTRACTS      size_usd = price * size * contract_multiplier
    spot -> BASE UNITS     size_usd = price * size
size_usd is NULL when we have no contract spec — a wrong-unit capacity number is
far worse than a missing one, and capacity is the entire point of this table.

Volume control: identical consecutive books carry no new capacity information and
are dropped; snapshots are additionally throttled per (exchange, symbol, market).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS carry_book_l2 (
    id       BIGSERIAL PRIMARY KEY,
    ts       TIMESTAMPTZ DEFAULT now(),
    exchange TEXT,
    symbol   TEXT,
    market   TEXT,              -- 'perp' | 'spot'
    side     TEXT,              -- 'bid'  | 'ask'
    level    INT,               -- 1 = touch … N
    price    DOUBLE PRECISION,
    size     DOUBLE PRECISION,  -- contracts (perp) | base units (spot)
    size_usd DOUBLE PRECISION   -- NULL when no contract spec
);
CREATE INDEX IF NOT EXISTS idx_carry_book_l2_sym_ts
    ON carry_book_l2 (exchange, symbol, market, ts);
"""

_FLUSH_ROWS = 400
_FLUSH_SECS = 2.0
# RUN 2 (2026-08-19): 120s, doubled from run 1's 60s to pay for doubling the
# universe (61 -> 129 names) inside the same disk budget. Worst-hour capacity
# needs hourly COVERAGE, not intra-minute resolution: 120s still gives 30
# snapshots/hour/stream, ~210 per hour-of-day bucket over 7 days, versus the
# ~40 that run 1's worst-hour analysis actually rested on.
#
# Measured from run 1 (not guessed): 137.4 bytes/row incl. index; a perp stream
# realises exactly 1.00 snapshot per throttle interval, a spot stream 0.80.
# 129 names x 2 markets at 120s -> ~16.7M rows/day -> ~2.30 GB/day.
_SNAP_MIN_INTERVAL = float(os.getenv("CARRY_SNAP_INTERVAL", "120"))


class CarryBookStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._rows: list[tuple] = []
        self._lock = asyncio.Lock()
        self._flusher: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_snap: dict[tuple, float] = {}
        self._last_book: dict[tuple, tuple] = {}
        self._mult: dict[tuple, float] = {}
        self.rows_written = 0
        self.snaps_written = 0
        self.snaps_skipped = 0

    def set_multipliers(self, mult: dict) -> None:
        self._mult = mult

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn, min_size=1, max_size=4, command_timeout=20.0,
            statement_cache_size=0,
        )
        await self._pool.execute(_SCHEMA)
        self._stop.clear()
        self._flusher = asyncio.create_task(self._flush_loop())
        logger.info("[carry/l2] connected, carry_book_l2 ready")

    async def close(self) -> None:
        self._stop.set()
        if self._flusher:
            self._flusher.cancel()
            try:
                await self._flusher
            except (asyncio.CancelledError, Exception):
                pass
            self._flusher = None
        await self.flush()
        if self._pool:
            await self._pool.close()
            self._pool = None
        logger.info("[carry/l2] closed — %d snapshots, %d rows",
                    self.snaps_written, self.rows_written)

    async def add_snapshot(self, exchange: str, symbol: str, market: str,
                           bids: list[tuple[float, float]],
                           asks: list[tuple[float, float]],
                           levels: int, ts=None) -> None:
        """Queue one top-N book. bids/asks are (price, size) best-first."""
        if not bids or not asks:
            return
        key = (exchange, symbol, market)
        now = time.monotonic()
        if now - self._last_snap.get(key, 0.0) < _SNAP_MIN_INTERVAL:
            return
        book = (tuple(bids[:levels]), tuple(asks[:levels]))
        if self._last_book.get(key) == book:
            self.snaps_skipped += 1
            return
        self._last_snap[key] = now
        self._last_book[key] = book

        if market == "perp":
            mult = self._mult.get((exchange, symbol.upper()))
        else:
            mult = 1.0                     # spot sizes are already base units

        import datetime as _dt
        stamp = ts or _dt.datetime.now(_dt.timezone.utc)
        for side, lv in (("bid", bids), ("ask", asks)):
            for i, (price, size) in enumerate(lv[:levels], start=1):
                usd = (price * size * mult) if mult else None
                self._rows.append((stamp, exchange, symbol, market, side,
                                   i, price, size, usd))
        self.snaps_written += 1
        if len(self._rows) >= _FLUSH_ROWS:
            await self.flush()

    async def _flush_loop(self) -> None:
        try:
            while not self._stop.is_set():
                await asyncio.sleep(_FLUSH_SECS)
                await self.flush()
        except asyncio.CancelledError:
            return

    async def flush(self) -> None:
        if not self._pool:
            return
        async with self._lock:
            rows, self._rows = self._rows, []
        if not rows:
            return
        try:
            async with self._pool.acquire() as con:
                await con.executemany(
                    "INSERT INTO carry_book_l2"
                    " (ts, exchange, symbol, market, side, level, price, size, size_usd)"
                    " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)", rows)
                self.rows_written += len(rows)
        except Exception as exc:
            logger.warning("[carry/l2] flush failed (%d rows): %r", len(rows), exc)
