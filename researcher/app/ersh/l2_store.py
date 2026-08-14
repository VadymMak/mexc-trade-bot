"""Batched writer for the ёрш L2 touch-depth table.

One additive table, self-healing (CREATE TABLE IF NOT EXISTS):

  ersh_book_l2 — top-5 bid and ask levels, one row per level

Ten rows (5 bid + 5 ask) share ONE `ts`, so a snapshot is recovered with
`GROUP BY exchange, symbol, ts` — there is no separate snapshot id.

`ts` holds the EXCHANGE event time, the same clock tape_prints and book_ticker
already use, so the tape can be replayed against the book it printed into.
That alignment is the whole point of the table: queue position can only be
reconstructed by matching a print to the book that stood in front of it.

`size` is CONTRACTS as the exchange sends it. `size_usd` = price × size ×
contract multiplier, and is NULL when we have no spec for that contract — a
wrong-unit queue depth is worse than a missing one.

Volume control (top-5 books are ~10× heavier than the tape):
  • at most _SNAP_MIN_INTERVAL apart per symbol
  • identical consecutive books are dropped — a book that did not change
    carries no queue information
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import asyncpg

from .store import ms_to_dt

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ersh_book_l2 (
    id       BIGSERIAL PRIMARY KEY,
    ts       TIMESTAMPTZ DEFAULT now(),
    exchange TEXT,
    symbol   TEXT,
    side     TEXT,              -- 'bid' | 'ask'
    level    INT,               -- 1 = touch … 5
    price    DOUBLE PRECISION,
    size     DOUBLE PRECISION,  -- CONTRACTS, as sent by the exchange
    size_usd DOUBLE PRECISION   -- price × size × multiplier, NULL if no spec
);
CREATE INDEX IF NOT EXISTS idx_ersh_book_l2_sym_ts ON ersh_book_l2 (exchange, symbol, ts);
"""

_FLUSH_ROWS = 400        # 40 snapshots' worth
_FLUSH_SECS = 2.0
_SNAP_MIN_INTERVAL = 0.5  # ≤2 snapshots/sec/symbol
_LEVELS = 5


class L2BookStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._rows: list[tuple] = []
        self._lock = asyncio.Lock()
        self._flusher: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_snap: dict[tuple, float] = {}   # (ex, sym) → monotonic
        self._last_book: dict[tuple, tuple] = {}   # (ex, sym) → last book written
        self._mult: dict[tuple, float] = {}        # (ex, SYMBOL) → units per contract
        self.rows_written = 0
        self.snaps_written = 0
        self.snaps_skipped = 0

    def set_multipliers(self, mult: dict) -> None:
        """Attach contract multipliers so size_usd is real. Missing → NULL."""
        self._mult = mult

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn, min_size=1, max_size=4, command_timeout=20.0,
            statement_cache_size=0,
        )
        await self._pool.execute(_SCHEMA)
        self._stop.clear()
        self._flusher = asyncio.create_task(self._flush_loop())
        logger.info("[ersh/l2] connected, schema ready")

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
        logger.info("[ersh/l2] closed — %d snapshots, %d rows written",
                    self.snaps_written, self.rows_written)

    # ── queueing ──────────────────────────────────────────────────────────────

    async def add_snapshot(self, exchange: str, symbol: str,
                           bids: list[tuple[float, float]],
                           asks: list[tuple[float, float]], ts_ms) -> None:
        """Queue one top-5 book. bids/asks are (price, size) best-first."""
        if not bids or not asks:
            return                     # one-sided book — nothing to queue behind

        key = (exchange, symbol)
        now = time.monotonic()
        if now - self._last_snap.get(key, 0.0) < _SNAP_MIN_INTERVAL:
            return
        book = (tuple(bids[:_LEVELS]), tuple(asks[:_LEVELS]))
        if self._last_book.get(key) == book:
            self.snaps_skipped += 1    # unchanged book — no queue information
            return
        self._last_snap[key] = now
        self._last_book[key] = book

        ts = ms_to_dt(ts_ms)           # one ts for all 10 rows = the snapshot key
        mult = self._mult.get((exchange, symbol.upper()))
        for side, levels in (("bid", bids), ("ask", asks)):
            for i, (price, size) in enumerate(levels[:_LEVELS], start=1):
                usd = (price * size * mult) if mult else None
                self._rows.append((ts, exchange, symbol, side, i, price, size, usd))
        self.snaps_written += 1
        if len(self._rows) >= _FLUSH_ROWS:
            await self.flush()

    # ── flushing ──────────────────────────────────────────────────────────────

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
                    "INSERT INTO ersh_book_l2"
                    " (ts, exchange, symbol, side, level, price, size, size_usd)"
                    " VALUES ($1,$2,$3,$4,$5,$6,$7,$8)", rows)
                self.rows_written += len(rows)
        except Exception as exc:
            # Same policy as the tape store: never lose the stream over a
            # transient DB error — drop the batch, log, keep collecting.
            logger.warning("[ersh/l2] flush failed (%d rows): %r", len(rows), exc)
