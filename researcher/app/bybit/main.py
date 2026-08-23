"""
Bybit funding / basis collector — ADDITIVE, READ-ONLY, DATA COLLECTION ONLY.

Purpose: evaluate Bybit as CARRY VENUE #2 (see skill trading-edge-lessons §17).
Public endpoints only. No keys, no orders, no private calls anywhere in this
module.

DELIBERATELY ISOLATED FROM THE BOT
    Writes to its own table `bybit_funding_snapshots`. Bybit rows are NOT added
    to `funding_basis_snapshots`, because the paper bot's selector reads that
    table and would immediately try to "trade" a venue it has no execution path
    for. Isolation is the whole point until Bybit is a real venue for us.

Every CYCLE_SECONDS:
  - 2 bulk ticker calls (linear perp + spot)
  - instrument lists refreshed on a TTL (universe + funding-interval truth)
  - join per symbol, compute basis_bps, spreads, annualised funding
  - batch-INSERT into bybit_funding_snapshots

FUNDING INTERVAL IS MEASURED, NEVER ASSUMED. Bybit publishes it per symbol in
two places (`instruments-info.fundingInterval` in MINUTES, and the linear
ticker's `fundingIntervalHour`); we take the instrument value as truth and
cross-check the ticker. In the current universe 190 names settle every 4h and
101 every 8h — hardcoding 8h would understate ~2/3 of the venue's APR by 2x,
which is the exact bug that cost us on MEXC/Gate.

UNIVERSE = EXACT SYMBOL MATCH, USDT-quoted, present and Trading in BOTH the
linear-perp and spot feeds. Matching on (baseCoin, quoteCoin) instead would add
38 pairs, but those are Bybit's USDC-settled `<COIN>PERP` contracts quoted
against `<COIN>USDC` spot — a different collateral currency. They are excluded
on purpose, not by accident.

CONTRACT UNITS: Bybit linear USDT perps are quantity-in-BASE-COIN, so there is
no contract multiplier to apply and `price x size` is meaningful (unlike Gate's
quanto_multiplier / MEXC's contractSize). Sizes are stored as BASE units to
match the sibling collector's convention. A price-ratio sanity gate still runs
every cycle so that a future 1000x-style listing cannot silently poison basis.

Run:  cd researcher && .venv/bin/python -m app.bybit.main
      cd researcher && .venv/bin/python -m app.bybit.main --once   (smoke)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import asyncpg

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bybit")

# ── Config ────────────────────────────────────────────────────────────────
CYCLE_SECONDS = int(os.getenv("BYBIT_CYCLE_SECONDS", "300"))
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)
INSTRUMENT_REFRESH_HOURS = float(os.getenv("BYBIT_INSTRUMENT_REFRESH_HOURS", "6"))

# Anti-zombie watchdog — same discipline as the carry collector. A service that
# is `active` while writing nothing is the failure mode that cost us ~40h of
# funding history; nothing here may wedge silently.
STALL_SOFT_SECS = float(os.getenv("BYBIT_STALL_SOFT_SECS", str(3 * CYCLE_SECONDS)))
STALL_HARD_SECS = float(os.getenv("BYBIT_STALL_HARD_SECS", str(6 * CYCLE_SECONDS)))
FETCH_RETRIES = int(os.getenv("BYBIT_FETCH_RETRIES", "3"))
FETCH_BACKOFF_SECS = (1.0, 4.0, 10.0)

# Guard against a unit-convention change (e.g. a 1000x multiplier listing)
# silently producing nonsense basis. Outside this band the pair is dropped for
# the cycle and counted, never written.
MIN_PX_RATIO = float(os.getenv("BYBIT_MIN_PX_RATIO", "0.9"))
MAX_PX_RATIO = float(os.getenv("BYBIT_MAX_PX_RATIO", "1.1"))

BASE = "https://api.bybit.com/v5/market/"
LINEAR_TICKERS = BASE + "tickers?category=linear"
SPOT_TICKERS = BASE + "tickers?category=spot"
LINEAR_INSTRUMENTS = BASE + "instruments-info?category=linear&limit=1000"
SPOT_INSTRUMENTS = BASE + "instruments-info?category=spot&limit=1000"

DSN = os.getenv("NEON_DATABASE_URL", "")


# ── Helpers ─────────────────────────────────────────────────────────────────
def _f(v: Any) -> Optional[float]:
    """Parse to float, None on missing/blank/unparseable — never fabricate."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def _spread_bps(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    m = _mid(bid, ask)
    if m is None or ask is None or bid is None:
        return None
    return (ask - bid) / m * 10000.0


def _basis_bps(perp_mark: Optional[float], spot: Optional[float]) -> Optional[float]:
    if perp_mark is None or spot is None or spot <= 0:
        return None
    return (perp_mark - spot) / spot * 10000.0


def _funding_apr_pct(rate: Optional[float], interval_h: Optional[float]) -> Optional[float]:
    """Annualise on the symbol's REAL interval. None if unknown — never default."""
    if rate is None or not interval_h or interval_h <= 0:
        return None
    return rate * (24.0 / interval_h) * 365.0 * 100.0


def _mins_to_settle(next_settle: Optional[datetime]) -> Optional[float]:
    if next_settle is None:
        return None
    return (next_settle - datetime.now(timezone.utc)).total_seconds() / 60.0


# ── Fetch ────────────────────────────────────────────────────────────────────
class FetchError(Exception):
    """One endpoint failed every retry. Carries the URL so the caller can name
    WHICH feed is down instead of logging an anonymous traceback."""

    def __init__(self, url: str, cause: BaseException) -> None:
        super().__init__(f"{url}: {cause!r}")
        self.url, self.cause = url, cause


async def _get_json(session: aiohttp.ClientSession, url: str,
                    retries: int | None = None) -> Any:
    """Fetch one bulk endpoint with backoff. Raises FetchError on give-up —
    never a sentinel, because an empty list reads downstream as "the venue
    listed nothing" and that is how you write a hole into history."""
    attempts = FETCH_RETRIES if retries is None else retries
    last: BaseException | None = None
    for i in range(attempts):
        try:
            async with session.get(url, timeout=HTTP_TIMEOUT) as r:
                r.raise_for_status()
                payload = await r.json()
            if payload.get("retCode") != 0:
                raise RuntimeError(f"retCode={payload.get('retCode')} "
                                   f"retMsg={payload.get('retMsg')!r}")
            return payload["result"]["list"]
        except Exception as exc:                      # noqa: BLE001 — re-raised below
            last = exc
            if i + 1 < attempts:
                await asyncio.sleep(FETCH_BACKOFF_SECS[min(i, len(FETCH_BACKOFF_SECS) - 1)])
    raise FetchError(url, last) from last


async def fetch_tickers(session: aiohttp.ClientSession) -> tuple:
    """Both ticker feeds INDEPENDENTLY: one dead feed must not discard the
    cycle. Returns (linear, spot, failures) with a failed feed as None."""
    urls = (LINEAR_TICKERS, SPOT_TICKERS)
    res = await asyncio.gather(*(_get_json(session, u) for u in urls),
                               return_exceptions=True)
    out, failures = [], []
    for url, r in zip(urls, res):
        if isinstance(r, BaseException):
            out.append(None)
            failures.append(r if isinstance(r, FetchError) else FetchError(url, r))
        else:
            out.append(r)
    return (*out, failures)


class Universe:
    """Dual-listed universe + per-symbol funding interval, refreshed on a TTL.

    A symbol whose interval cannot be resolved is left ABSENT, never defaulted
    to 8h.
    """

    def __init__(self) -> None:
        self.symbols: set[str] = set()
        self.interval_h: dict[str, float] = {}
        self.source: dict[str, str] = {}
        self._last_refresh: float = -1e9

    def due(self, now_mono: float) -> bool:
        return (now_mono - self._last_refresh) >= INSTRUMENT_REFRESH_HOURS * 3600.0

    async def refresh(self, session: aiohttp.ClientSession, now_mono: float) -> int:
        try:
            lin = await _get_json(session, LINEAR_INSTRUMENTS)
            spo = await _get_json(session, SPOT_INSTRUMENTS)
        except FetchError as exc:
            log.warning("[bybit] instrument refresh failed: %s (keeping previous "
                        "universe of %d)", exc, len(self.symbols))
            return 0

        perp = {i["symbol"]: i for i in lin
                if i.get("contractType") == "LinearPerpetual"
                and i.get("status") == "Trading"
                and i.get("quoteCoin") == "USDT"}
        spot = {i["symbol"] for i in spo if i.get("status") == "Trading"}

        symbols, interval_h, source = set(), {}, {}
        for sym in perp.keys() & spot:
            mins = _f(perp[sym].get("fundingInterval"))
            if not mins or mins <= 0:
                continue                    # unresolved interval => not collected
            symbols.add(sym)
            interval_h[sym] = mins / 60.0
            source[sym] = "bybit.instruments.fundingInterval"

        if symbols:
            self.symbols, self.interval_h, self.source = symbols, interval_h, source
            self._last_refresh = now_mono
            from collections import Counter
            dist = Counter(f"{v:g}h" for v in interval_h.values())
            log.info("[bybit] universe refreshed: %d dual-listed USDT names; "
                     "funding intervals %s", len(symbols), dict(dist))
        return len(symbols)


# ── DB ───────────────────────────────────────────────────────────────────────
# Columns MIRROR funding_basis_snapshots so the existing carry analysis scripts
# can be pointed here with a table-name change and little else.
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS bybit_funding_snapshots (
    id                     BIGSERIAL PRIMARY KEY,
    ts                     TIMESTAMPTZ      DEFAULT now(),
    exchange               TEXT,
    symbol                 TEXT,
    perp_mark              DOUBLE PRECISION,
    spot_price             DOUBLE PRECISION,
    basis_bps              DOUBLE PRECISION,
    funding_rate           DOUBLE PRECISION,
    funding_interval_hours DOUBLE PRECISION,
    mins_to_funding        DOUBLE PRECISION,
    funding_annualized_pct DOUBLE PRECISION,
    perp_bid               DOUBLE PRECISION,
    perp_ask               DOUBLE PRECISION,
    perp_spread_bps        DOUBLE PRECISION,
    spot_bid               DOUBLE PRECISION,
    spot_ask               DOUBLE PRECISION,
    spot_spread_bps        DOUBLE PRECISION,
    perp_depth5_usd        DOUBLE PRECISION,
    spot_depth5_usd        DOUBLE PRECISION,
    next_settle_time       TIMESTAMPTZ,
    interval_source        TEXT,
    perp_volume24_base     DOUBLE PRECISION,
    perp_volume24_usd      DOUBLE PRECISION,
    perp_open_interest     DOUBLE PRECISION,
    spot_volume24_base     DOUBLE PRECISION,
    spot_volume24_usd      DOUBLE PRECISION,
    perp_bid_size          DOUBLE PRECISION,
    perp_ask_size          DOUBLE PRECISION,
    spot_bid_size          DOUBLE PRECISION,
    spot_ask_size          DOUBLE PRECISION,
    contract_multiplier    DOUBLE PRECISION,
    perp_index_price       DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_bfs_ts     ON bybit_funding_snapshots (ts DESC);
CREATE INDEX IF NOT EXISTS idx_bfs_symbol ON bybit_funding_snapshots (symbol, ts DESC);

CREATE TABLE IF NOT EXISTS bybit_collector_health (
    id                    BIGSERIAL PRIMARY KEY,
    ts                    TIMESTAMPTZ DEFAULT now(),
    cycle                 BIGINT,
    rows_written          INTEGER,
    universe_size         INTEGER,
    skipped_px_ratio      INTEGER,
    failed_endpoints      TEXT,
    secs_since_last_write DOUBLE PRECISION,
    consecutive_failures  INTEGER,
    action                TEXT
);
CREATE INDEX IF NOT EXISTS idx_bch_ts ON bybit_collector_health (ts DESC);
"""

INSERT_SQL = """
INSERT INTO bybit_funding_snapshots
    (exchange, symbol, perp_mark, spot_price, basis_bps, funding_rate,
     funding_interval_hours, mins_to_funding, funding_annualized_pct,
     perp_bid, perp_ask, perp_spread_bps, spot_bid, spot_ask, spot_spread_bps,
     perp_depth5_usd, spot_depth5_usd, next_settle_time, interval_source,
     perp_volume24_base, perp_volume24_usd, perp_open_interest,
     spot_volume24_base, spot_volume24_usd,
     perp_bid_size, perp_ask_size, spot_bid_size, spot_ask_size,
     contract_multiplier, perp_index_price)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
        $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30)
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as con:
        await con.execute(CREATE_SQL)


async def insert_rows(pool: asyncpg.Pool, rows: list[tuple]) -> int:
    if not rows:
        return 0
    async with pool.acquire() as con:
        await con.executemany(INSERT_SQL, rows)
    return len(rows)


async def write_health(pool: asyncpg.Pool, **kw) -> None:
    """Best-effort heartbeat: it must never mask the error that produced it."""
    try:
        await pool.execute(
            """INSERT INTO bybit_collector_health
                   (cycle, rows_written, universe_size, skipped_px_ratio,
                    failed_endpoints, secs_since_last_write,
                    consecutive_failures, action)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            kw.get("cycle"), kw.get("rows_written"), kw.get("universe_size"),
            kw.get("skipped_px_ratio"), kw.get("failed_endpoints"),
            kw.get("secs_since_last_write"), kw.get("consecutive_failures"),
            kw.get("action"))
    except Exception as exc:                          # noqa: BLE001
        log.warning("[bybit] health write failed: %r", exc)


# ── Build rows ───────────────────────────────────────────────────────────────
def build_rows(linear: list | None, spot: list | None,
               uni: Universe) -> tuple[list[tuple], dict]:
    """Join the two ticker feeds over the dual-listed universe.

    A row is written only when BOTH legs price. Bybit linear USDT perps are
    quantity-in-base-coin, so sizes are stored as base units and no contract
    multiplier applies (NULL, deliberately — not 1.0, which would assert a
    convention we did not measure).
    """
    stats = {"rows": 0, "skipped_px_ratio": 0, "skipped_unpriced": 0}
    if linear is None or spot is None:
        return [], stats

    lt = {t["symbol"]: t for t in linear}
    st = {t["symbol"]: t for t in spot}
    rows: list[tuple] = []

    for sym in sorted(uni.symbols):
        p, s = lt.get(sym), st.get(sym)
        if p is None or s is None:
            continue

        perp_mark = _f(p.get("markPrice"))
        perp_bid, perp_ask = _f(p.get("bid1Price")), _f(p.get("ask1Price"))
        spot_bid, spot_ask = _f(s.get("bid1Price")), _f(s.get("ask1Price"))
        spot_price = _mid(spot_bid, spot_ask)
        if perp_mark is None or spot_price is None:
            stats["skipped_unpriced"] += 1
            continue

        # Unit-convention gate: identical symbols must price alike. A 1000x
        # style listing would show up here rather than as a silent 10,000 bps
        # "basis" nobody questions.
        ratio = perp_mark / spot_price
        if not (MIN_PX_RATIO <= ratio <= MAX_PX_RATIO):
            stats["skipped_px_ratio"] += 1
            continue

        iv = uni.interval_h.get(sym)
        rate = _f(p.get("fundingRate"))
        nxt = _f(p.get("nextFundingTime"))
        next_settle = (datetime.fromtimestamp(nxt / 1000.0, timezone.utc)
                       if nxt else None)
        perp_vol_base = _f(p.get("volume24h"))
        perp_vol_usd = _f(p.get("turnover24h"))
        spot_vol_base = _f(s.get("volume24h"))
        spot_vol_usd = _f(s.get("turnover24h"))

        rows.append((
            "bybit", sym, perp_mark, spot_price,
            _basis_bps(perp_mark, spot_price), rate,
            iv, _mins_to_settle(next_settle), _funding_apr_pct(rate, iv),
            perp_bid, perp_ask, _spread_bps(perp_bid, perp_ask),
            spot_bid, spot_ask, _spread_bps(spot_bid, spot_ask),
            None, None,                    # depth5 not collected here
            next_settle, uni.source.get(sym),
            perp_vol_base, perp_vol_usd, _f(p.get("openInterest")),
            spot_vol_base, spot_vol_usd,
            _f(p.get("bid1Size")), _f(p.get("ask1Size")),
            _f(s.get("bid1Size")), _f(s.get("ask1Size")),
            None,                          # no contract multiplier: qty is base
            _f(p.get("indexPrice")),
        ))

    stats["rows"] = len(rows)
    return rows, stats


# ── Main loop ────────────────────────────────────────────────────────────────
class Stalled(RuntimeError):
    """Nothing written for STALL_HARD_SECS — exit so systemd restarts clean."""


async def _cycle(session: aiohttp.ClientSession, pool: asyncpg.Pool,
                 uni: Universe, t0: float) -> tuple[int, dict, list[FetchError]]:
    if uni.due(t0):
        await uni.refresh(session, t0)

    linear, spot, failures = await fetch_tickers(session)
    rows, stats = build_rows(linear, spot, uni)
    n = await insert_rows(pool, rows)
    log.info("[bybit] Inserted %d rows (universe=%d, unpriced=%d, "
             "px-ratio-skipped=%d)%s",
             n, len(uni.symbols), stats["skipped_unpriced"],
             stats["skipped_px_ratio"],
             ("  DEGRADED: " + ", ".join(f.url for f in failures)) if failures else "")
    return n, stats, failures


async def run(once: bool = False) -> None:
    if not DSN:
        raise RuntimeError("NEON_DATABASE_URL not set (check researcher/.env)")

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=3,
                                     command_timeout=20.0, statement_cache_size=0)
    await ensure_schema(pool)
    log.info("[bybit] connected; bybit_funding_snapshots ready; cycle=%ds; "
             "instrument refresh=%.1fh; watchdog soft=%.0fs hard=%.0fs "
             "| PUBLIC READ-ONLY, no keys, no orders",
             CYCLE_SECONDS, INSTRUMENT_REFRESH_HOURS, STALL_SOFT_SECS, STALL_HARD_SECS)

    loop = asyncio.get_event_loop()
    session = aiohttp.ClientSession()
    uni = Universe()
    last_write = loop.time()
    consecutive_failures = 0
    cycle = 0

    try:
        while True:
            cycle += 1
            t0 = loop.time()
            n, stats, failures = 0, {"skipped_px_ratio": 0}, []
            action = "ok"
            try:
                n, stats, failures = await _cycle(session, pool, uni, t0)
                if n:
                    last_write = loop.time()
                    consecutive_failures = 0
                    action = "partial" if failures else "ok"
                else:
                    consecutive_failures += 1
                    action = "partial"
                    log.error("[bybit] cycle %d wrote 0 rows (failed: %s)", cycle,
                              ", ".join(f.url for f in failures) or "none")
            except Exception as exc:                  # noqa: BLE001 — loud, counted
                consecutive_failures += 1
                action = "error"
                log.exception("[bybit] cycle %d FAILED (%d in a row): %r",
                              cycle, consecutive_failures, exc)

            stalled_for = loop.time() - last_write
            if stalled_for > STALL_HARD_SECS and not once:
                action = "hard-stall"
                await write_health(
                    pool, cycle=cycle, rows_written=n, universe_size=len(uni.symbols),
                    skipped_px_ratio=stats.get("skipped_px_ratio"),
                    failed_endpoints=", ".join(f.url for f in failures) or None,
                    secs_since_last_write=stalled_for,
                    consecutive_failures=consecutive_failures, action=action)
                log.error("[bybit] HARD STALL: no write for %.0fs (limit %.0fs) "
                          "— EXITING for a clean systemd restart",
                          stalled_for, STALL_HARD_SECS)
                raise Stalled(f"no write for {stalled_for:.0f}s")

            if stalled_for > STALL_SOFT_SECS and not once:
                action = "soft-stall"
                log.error("[bybit] SOFT STALL: no write for %.0fs (limit %.0fs) "
                          "— rebuilding HTTP session (fresh connector + DNS)",
                          stalled_for, STALL_SOFT_SECS)
                try:
                    await session.close()
                except Exception as exc:              # noqa: BLE001
                    log.warning("[bybit] session close failed: %r", exc)
                session = aiohttp.ClientSession()

            await write_health(
                pool, cycle=cycle, rows_written=n, universe_size=len(uni.symbols),
                skipped_px_ratio=stats.get("skipped_px_ratio"),
                failed_endpoints=", ".join(f.url for f in failures) or None,
                secs_since_last_write=stalled_for,
                consecutive_failures=consecutive_failures, action=action)

            if once:
                return
            elapsed = loop.time() - t0
            await asyncio.sleep(max(1.0, CYCLE_SECONDS - elapsed))
    finally:
        await session.close()
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one cycle then exit")
    args = ap.parse_args()
    try:
        asyncio.run(run(once=args.once))
    except KeyboardInterrupt:
        log.info("[bybit] stopped by user")
    except Stalled as exc:
        log.error("[bybit] exiting on watchdog: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
