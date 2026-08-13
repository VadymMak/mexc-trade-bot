"""Batched writer for the ёрш raw-tape tables.

Two additive tables, both self-healing (CREATE TABLE IF NOT EXISTS):

  tape_prints  — one row per trade print
  book_ticker  — best bid/ask, throttled to ~1 row/sec/coin

`ts` holds the EXCHANGE event time (not insert time), so prints and quotes share
one clock and can be replayed in order. The DEFAULT now() is only a fallback for
messages that arrive without a usable timestamp.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tape_prints (
    id       BIGSERIAL PRIMARY KEY,
    ts       TIMESTAMPTZ DEFAULT now(),
    exchange TEXT,
    symbol   TEXT,
    price    DOUBLE PRECISION,
    size     DOUBLE PRECISION,
    side     TEXT   -- 'buy' = aggressor hit the ASK (up), 'sell' = hit the BID (down)
);
CREATE INDEX IF NOT EXISTS idx_tape_prints_sym_ts ON tape_prints (exchange, symbol, ts);

CREATE TABLE IF NOT EXISTS book_ticker (
    id       BIGSERIAL PRIMARY KEY,
    ts       TIMESTAMPTZ DEFAULT now(),
    exchange TEXT,
    symbol   TEXT,
    best_bid DOUBLE PRECISION,
    best_ask DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_book_ticker_sym_ts ON book_ticker (exchange, symbol, ts);
"""

_FLUSH_ROWS = 200      # flush once this many rows are queued
_FLUSH_SECS = 2.0      # ...or this often, whichever comes first


def ms_to_dt(ms: Optional[float]) -> datetime:
    """Exchange epoch-ms → aware UTC datetime, falling back to now()."""
    try:
        v = float(ms)
        if v > 1e11:          # milliseconds
            v /= 1000.0
        if v > 1e6:           # plausible epoch seconds
            return datetime.fromtimestamp(v, tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    return datetime.now(timezone.utc)


class TapeStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._prints: list[tuple] = []
        self._quotes: list[tuple] = []
        self._lock = asyncio.Lock()
        self._flusher: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.prints_written = 0
        self.quotes_written = 0

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn, min_size=1, max_size=4, command_timeout=20.0,
            statement_cache_size=0,
        )
        await self._pool.execute(_SCHEMA)
        self._stop.clear()
        self._flusher = asyncio.create_task(self._flush_loop())
        logger.info("[ersh/db] connected, schema ready")

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
        logger.info("[ersh/db] closed — %d prints, %d quotes written",
                    self.prints_written, self.quotes_written)

    # ── queueing ──────────────────────────────────────────────────────────────

    async def add_print(self, exchange: str, symbol: str, price: float,
                        size: float, side: str, ts_ms) -> None:
        self._prints.append((ms_to_dt(ts_ms), exchange, symbol, price, size, side))
        if len(self._prints) >= _FLUSH_ROWS:
            await self.flush()

    async def add_quote(self, exchange: str, symbol: str, bid: float,
                        ask: float, ts_ms) -> None:
        self._quotes.append((ms_to_dt(ts_ms), exchange, symbol, bid, ask))
        if len(self._quotes) >= _FLUSH_ROWS:
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
            prints, self._prints = self._prints, []
            quotes, self._quotes = self._quotes, []
        if not prints and not quotes:
            return
        try:
            async with self._pool.acquire() as con:
                if prints:
                    await con.executemany(
                        "INSERT INTO tape_prints (ts, exchange, symbol, price, size, side)"
                        " VALUES ($1,$2,$3,$4,$5,$6)", prints)
                    self.prints_written += len(prints)
                if quotes:
                    await con.executemany(
                        "INSERT INTO book_ticker (ts, exchange, symbol, best_bid, best_ask)"
                        " VALUES ($1,$2,$3,$4,$5)", quotes)
                    self.quotes_written += len(quotes)
        except Exception as exc:
            # Never lose the stream over a transient DB error — drop the batch,
            # log, and keep collecting.
            logger.warning("[ersh/db] flush failed (%d prints, %d quotes): %r",
                           len(prints), len(quotes), exc)
