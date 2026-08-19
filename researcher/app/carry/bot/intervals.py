"""Real funding-interval resolution — READ-ONLY public endpoints.

WHY THIS EXISTS: `funding_basis_snapshots.funding_interval_hours` is **8 in all
7,749,822 rows**. It is a hardcode in the collector, not a measurement. 95 of
the 129 carry qualifiers actually settle every 4h, so a bot that trusts that
column would (a) understate APR ~2x and (b) accrue funding at the wrong times,
which corrupts the realised-vs-modelled comparison that the whole paper run
exists to produce.

Sources (both public, no authentication, no trading scope):
    Gate : GET /api/v4/futures/usdt/contracts/{c}  -> funding_interval (SECONDS)
    MEXC : GET /api/v1/contract/funding_rate/{s}   -> collectCycle (HOURS)

Cached in `carry_funding_intervals` (self-healing CREATE TABLE IF NOT EXISTS)
so a venue outage cannot silently revert the bot to a wrong default. A name
whose interval cannot be resolved is EXCLUDED from selection rather than
guessed at.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS carry_funding_intervals (
    exchange       TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    interval_hours DOUBLE PRECISION,
    source         TEXT,
    fetched_ts     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (exchange, symbol)
);
"""
_MAX_AGE_HOURS = 24.0


def _get(url: str, timeout: float = 15.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _fetch_blocking(ex: str, sym: str) -> tuple[float | None, str]:
    """Never guesses. Returns (hours, source) or (None, reason)."""
    try:
        if ex == "gate":
            d = _get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{sym}")
            return float(d["funding_interval"]) / 3600.0, "gate.funding_interval"
        d = _get(f"https://contract.mexc.com/api/v1/contract/funding_rate/{sym}")["data"]
        return float(d["collectCycle"]), "mexc.collectCycle"
    except Exception as exc:
        return None, f"FETCH-FAILED {exc!r}"


class IntervalResolver:
    def __init__(self, pool) -> None:
        self._pool = pool
        self._mem: dict[tuple[str, str], float] = {}

    async def ensure_schema(self) -> None:
        await self._pool.execute(_SCHEMA)

    async def get(self, ex: str, sym: str) -> float | None:
        key = (ex, sym)
        if key in self._mem:
            return self._mem[key]
        row = await self._pool.fetchrow(
            """SELECT interval_hours,
                      extract(epoch FROM (now() - fetched_ts))/3600.0 AS age_h
               FROM carry_funding_intervals WHERE exchange=$1 AND symbol=$2""",
            ex, sym)
        if row and row["interval_hours"] and row["age_h"] < _MAX_AGE_HOURS:
            self._mem[key] = float(row["interval_hours"])
            return self._mem[key]

        hours, source = await asyncio.get_running_loop().run_in_executor(
            None, _fetch_blocking, ex, sym)
        if hours is None:
            # Fall back to a STALE cached value if one exists — still measured,
            # just old. Never fall back to a guess.
            if row and row["interval_hours"]:
                logger.warning("[carry/bot] %s/%s interval refresh failed (%s) — "
                               "using cached %.1fh", ex, sym, source,
                               row["interval_hours"])
                self._mem[key] = float(row["interval_hours"])
                return self._mem[key]
            logger.warning("[carry/bot] %s/%s interval unresolved (%s) — EXCLUDED",
                           ex, sym, source)
            return None
        await self._pool.execute(
            """INSERT INTO carry_funding_intervals
                   (exchange, symbol, interval_hours, source, fetched_ts)
               VALUES ($1,$2,$3,$4, now())
               ON CONFLICT (exchange, symbol) DO UPDATE
                   SET interval_hours=EXCLUDED.interval_hours,
                       source=EXCLUDED.source, fetched_ts=now()""",
            ex, sym, hours, source)
        self._mem[key] = hours
        return hours

    @staticmethod
    def epoch_index(ts, interval_hours: float) -> int:
        """Which funding epoch a timestamp falls in.

        Venues settle on a fixed UTC grid (4h -> 00/04/08/12/16/20), so the
        epoch is just floor(unix_seconds / interval_seconds). Using the REAL
        interval here is what makes accruals land at the right times.
        """
        return int(ts.timestamp() // int(interval_hours * 3600))

    @staticmethod
    def epoch_start(idx: int, interval_hours: float):
        import datetime as _dt
        return _dt.datetime.fromtimestamp(idx * int(interval_hours * 3600),
                                          tz=_dt.timezone.utc)
