"""
Carry (funding / basis) data collector — ADDITIVE, data collection only.

Every CYCLE_SECONDS:
  - 5 bulk REST calls (MEXC perp+spot bookTicker+spot 24hr, Gate perp+spot)
  - join per (exchange, symbol)
  - compute basis_bps, funding_annualized_pct, perp/spot spread_bps
  - persist reported 24h volume + open interest + top-of-book sizes (both legs)
  - batch-INSERT into funding_basis_snapshots (new table, self-healing CREATE)

FUNDING INTERVAL (fixed 2026-08-19): the interval is MEASURED per symbol from
the venue bulk endpoints, never assumed. `funding_interval_hours` and
`next_settle_time` are the INPUTS to `funding_annualized_pct` /
`mins_to_funding` and are stored on the same row, so every derived value is
reproducible from the row itself. A symbol whose interval cannot be resolved
gets NULL in all three — it is never defaulted to 8h.

Does NOT touch arb tables. No trading. Read-only against exchanges.

Run:  cd researcher && .venv/bin/python -m app.carry.main
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import asyncpg

try:
    from dotenv import load_dotenv
    load_dotenv()  # foreground runs; systemd also injects via EnvironmentFile
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("carry")

# ── Config ────────────────────────────────────────────────────────────────
CYCLE_SECONDS = int(os.getenv("CARRY_CYCLE_SECONDS", "300"))
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

# ── Watchdog (added 2026-08-23) ────────────────────────────────────────────
# A collector that is `active` but writing nothing is the failure mode that
# cost us ~40h of funding history: 489 cycles in 4 days died on a single MEXC
# 403 and the loop just slept and retried forever. Nothing may wedge silently.
#   soft stall -> tear down and rebuild the HTTP session (fresh connector+DNS)
#   hard stall -> exit non-zero so systemd Restart=always gives a clean process
STALL_SOFT_SECS = float(os.getenv("CARRY_STALL_SOFT_SECS", str(3 * CYCLE_SECONDS)))
STALL_HARD_SECS = float(os.getenv("CARRY_STALL_HARD_SECS", str(6 * CYCLE_SECONDS)))
FETCH_RETRIES = int(os.getenv("CARRY_FETCH_RETRIES", "3"))
FETCH_BACKOFF_SECS = (1.0, 4.0, 10.0)

# The MEXC spot 24h-volume call is a SECOND heavy request to the Akamai-fronted
# host that rate-limit-403s us. 24h rolling volume barely moves, so poll it
# every Nth cycle instead of every cycle — less pressure on the blocked host.
VOLUME_EVERY_N = int(os.getenv("CARRY_VOLUME_EVERY_N", "4"))

# Funding intervals drift (~2.1% of symbols change interval per 23 days), so the
# map is REFRESHED on a TTL rather than cached once.
INTERVAL_REFRESH_HOURS = float(os.getenv("CARRY_INTERVAL_REFRESH_HOURS", "6"))

# Universe is built DYNAMICALLY each cycle from the bulk responses (below):
# every symbol that appears in BOTH the perp and spot bulk feed on an exchange.
# No hardcoded coin list.

MEXC_PERP_URL = "https://contract.mexc.com/api/v1/contract/ticker"
MEXC_SPOT_URL = "https://api.mexc.com/api/v3/ticker/bookTicker"
GATE_PERP_URL = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
GATE_SPOT_URL = "https://api.gateio.ws/api/v4/spot/tickers"
# volume-only lookup for the MEXC spot leg (bookTicker carries no volume).
# NEVER used to build the universe — see build_rows().
MEXC_SPOT_24H_URL = "https://api.mexc.com/api/v3/ticker/24hr"
# bulk funding-interval ground truth (one call each, refreshed on TTL)
GATE_CONTRACTS_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
MEXC_FUNDING_URL   = "https://contract.mexc.com/api/v1/contract/funding_rate"

DSN = os.getenv("NEON_DATABASE_URL", "")


# ── Helpers ─────────────────────────────────────────────────────────────────
def _f(v: Any) -> Optional[float]:
    """Parse to float, return None on missing/blank/unparseable — never fabricate."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


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


def _funding_apr_pct(funding_rate: Optional[float],
                     interval_h: Optional[float]) -> Optional[float]:
    """Annualise using the symbol's REAL settlement interval. None if unknown —
    never fall back to a default, which is the bug this replaces."""
    if funding_rate is None or not interval_h or interval_h <= 0:
        return None
    return funding_rate * (24.0 / interval_h) * 365.0 * 100.0


def _mins_to_settle(next_settle: Optional[datetime]) -> Optional[float]:
    """Minutes until the venue's own next settlement timestamp. None if unknown."""
    if next_settle is None:
        return None
    return (next_settle - datetime.now(timezone.utc)).total_seconds() / 60.0


class IntervalMap:
    """Bulk funding-interval + next-settle-time ground truth, refreshed on a TTL.

    Uses the BULK endpoints (one call per venue) rather than the per-symbol
    lookups in app/carry/bot/intervals.py, because this collector covers ~1,200
    symbols every cycle. Values are upserted into the same
    `carry_funding_intervals` cache the bot reads, so both share one source.
    A symbol absent from the venue response is left ABSENT, never defaulted.
    """

    def __init__(self) -> None:
        self.interval: dict[tuple[str, str], float] = {}
        self.next_settle: dict[tuple[str, str], datetime] = {}
        self.source: dict[tuple[str, str], str] = {}
        self._last_refresh: float = -1e9

    def due(self, now_mono: float) -> bool:
        return (now_mono - self._last_refresh) >= INTERVAL_REFRESH_HOURS * 3600.0

    async def refresh(self, session: aiohttp.ClientSession, now_mono: float) -> int:
        got = 0
        try:
            g = await _get_json(session, GATE_CONTRACTS_URL)
            for c in g:
                nm, fi = c.get("name"), c.get("funding_interval")
                if not nm or not fi:
                    continue
                k = ("gate", nm)
                self.interval[k] = float(fi) / 3600.0
                self.source[k] = "gate.funding_interval"
                na = c.get("funding_next_apply")
                if na:
                    self.next_settle[k] = datetime.fromtimestamp(float(na), timezone.utc)
                got += 1
        except Exception as exc:
            log.warning("[carry] gate interval refresh failed: %r (keeping previous map)", exc)
        try:
            m = await _get_json(session, MEXC_FUNDING_URL)
            for c in m.get("data", []):
                sym, cc = c.get("symbol"), c.get("collectCycle")
                if not sym or not cc:
                    continue
                k = ("mexc", sym)
                self.interval[k] = float(cc)
                self.source[k] = "mexc.collectCycle"
                ns = c.get("nextSettleTime")
                if ns:
                    self.next_settle[k] = datetime.fromtimestamp(float(ns) / 1000.0, timezone.utc)
                got += 1
        except Exception as exc:
            log.warning("[carry] mexc interval refresh failed: %r (keeping previous map)", exc)
        if got:
            self._last_refresh = now_mono
        return got

    async def persist(self, pool: asyncpg.Pool) -> None:
        """Upsert into the shared cache the carry bot reads."""
        if not self.interval:
            return
        recs = [(ex, sym, hrs, self.source.get((ex, sym), "bulk"))
                for (ex, sym), hrs in self.interval.items()]
        async with pool.acquire() as con:
            await con.executemany(
                """INSERT INTO carry_funding_intervals
                       (exchange, symbol, interval_hours, source, fetched_ts)
                   VALUES ($1,$2,$3,$4, now())
                   ON CONFLICT (exchange, symbol) DO UPDATE
                       SET interval_hours=EXCLUDED.interval_hours,
                           source=EXCLUDED.source, fetched_ts=now()""", recs)


# ── Fetch ────────────────────────────────────────────────────────────────────
class FetchError(Exception):
    """One bulk endpoint failed every retry. Carries the URL so the caller can
    say WHICH feed is down rather than logging an anonymous traceback."""

    def __init__(self, url: str, cause: BaseException) -> None:
        super().__init__(f"{url}: {cause!r}")
        self.url, self.cause = url, cause


async def _get_json(session: aiohttp.ClientSession, url: str,
                    retries: int | None = None) -> Any:
    """Fetch one bulk endpoint, retrying transient failures with backoff.

    Raises FetchError on give-up — never returns a sentinel, because an empty
    dict is indistinguishable from "the venue listed nothing" downstream and
    that is exactly how you silently write a hole into the history.
    """
    attempts = FETCH_RETRIES if retries is None else retries
    last: BaseException | None = None
    for i in range(attempts):
        try:
            async with session.get(url, timeout=HTTP_TIMEOUT) as r:
                r.raise_for_status()
                return await r.json()
        except Exception as exc:                      # noqa: BLE001 — re-raised below
            last = exc
            if i + 1 < attempts:
                await asyncio.sleep(FETCH_BACKOFF_SECS[min(i, len(FETCH_BACKOFF_SECS) - 1)])
    raise FetchError(url, last) from last


async def fetch_all(session: aiohttp.ClientSession) -> tuple:
    """Fetch the four bulk feeds INDEPENDENTLY.

    THE BUG THIS REPLACES: a bare asyncio.gather let one endpoint's exception
    propagate, so a single `403 Forbidden` on the MEXC spot bookTicker threw
    away the ENTIRE cycle — including the ~700 Gate rows that had fetched
    perfectly. 489 cycles (~40h of funding history) were lost that way between
    2026-08-19 and 2026-08-23.

    Now a dead feed only removes ITS OWN venue's rows. Returns
    (mexc_perp, mexc_spot, gate_perp, gate_spot, failures); a failed feed is
    None and every failure is named in `failures`.
    """
    urls = (MEXC_PERP_URL, MEXC_SPOT_URL, GATE_PERP_URL, GATE_SPOT_URL)
    res = await asyncio.gather(
        *(_get_json(session, u) for u in urls), return_exceptions=True)
    out, failures = [], []
    for url, r in zip(urls, res):
        if isinstance(r, BaseException):
            out.append(None)
            failures.append(FetchError(url, r) if not isinstance(r, FetchError) else r)
        else:
            out.append(r)
    return (*out, failures)


async def fetch_mexc_spot_volume(session: aiohttp.ClientSession) -> dict | None:
    """MEXC spot bookTicker carries no volume, so pull it separately.

    BEST EFFORT and explicitly so: this is a volume side-lookup, never part of
    the universe (see build_rows), so it must not be able to fail a cycle.
    Returns None on failure — the caller keeps the PREVIOUS cycle's volumes
    rather than writing NULLs over good data.
    """
    try:
        d = await _get_json(session, MEXC_SPOT_24H_URL, retries=2)
        return {x["symbol"]: x for x in d if x.get("symbol")}
    except FetchError as exc:
        log.warning("[carry] mexc spot 24h volume unavailable: %s "
                    "(keeping previous volumes)", exc)
        return None


# ── Build rows ───────────────────────────────────────────────────────────────
def _row(exchange, sym, perp_mark, spot_price, funding,
         perp_bid, perp_ask, spot_bid, spot_ask,
         interval_h, next_settle, interval_src,
         perp_vol_base, perp_vol_usd, perp_oi,
         spot_vol_base, spot_vol_usd,
         perp_bid_sz, perp_ask_sz, spot_bid_sz, spot_ask_sz,
         contract_mult) -> tuple:
    return (
        exchange, sym, perp_mark, spot_price,
        _basis_bps(perp_mark, spot_price), funding,
        # INPUTS stored beside the OUTPUTS they produce:
        (int(round(interval_h)) if interval_h else None),
        _mins_to_settle(next_settle),
        _funding_apr_pct(funding, interval_h),
        perp_bid, perp_ask, _spread_bps(perp_bid, perp_ask),
        spot_bid, spot_ask, _spread_bps(spot_bid, spot_ask),
        None, None,               # perp_depth5_usd, spot_depth5_usd (still NULL here)
        next_settle, interval_src,
        perp_vol_base, perp_vol_usd, perp_oi,
        spot_vol_base, spot_vol_usd,
        perp_bid_sz, perp_ask_sz, spot_bid_sz, spot_ask_sz,
        contract_mult,
    )


def build_rows(mexc_perp, mexc_spot, gate_perp, gate_spot,
               ivmap=None, mexc_spot_vol=None) -> tuple[list[tuple], dict]:
    """
    Dynamic universe: for each exchange, take every symbol present in BOTH the
    perp and spot bulk feed. Insert a row only if it has a numeric spot price
    AND a numeric perp mark. funding_rate may be NULL (never fabricated).
    Returns (rows, per-exchange coin counts) for logging.

    UNIVERSE LOGIC IS UNCHANGED. `mexc_spot_vol` is a volume-only side lookup and
    is NEVER consulted when deciding whether a symbol enters the universe.
    """
    # A None feed means "that endpoint failed this cycle" — it degrades to an
    # empty map, so the venue contributes no rows and the OTHER venue is
    # unaffected. It is never confused with "the venue listed nothing".
    mp = {d["symbol"]: d for d in (mexc_perp or {}).get("data", [])}   # perp: BTC_USDT
    ms = {d["symbol"]: d for d in (mexc_spot or [])}            # spot: BTCUSDT (no sep)
    gp = {d["contract"]: d for d in (gate_perp or [])}          # perp: BTC_USDT
    gs = {d["currency_pair"]: d for d in (gate_spot or [])}     # spot: BTC_USDT
    msv = mexc_spot_vol or {}

    iv_get = (lambda ex, sy: ivmap.interval.get((ex, sy))) if ivmap else (lambda ex, sy: None)
    ns_get = (lambda ex, sy: ivmap.next_settle.get((ex, sy))) if ivmap else (lambda ex, sy: None)
    sr_get = (lambda ex, sy: ivmap.source.get((ex, sy))) if ivmap else (lambda ex, sy: None)

    rows: list[tuple] = []
    counts = {"mexc": 0, "gate": 0}

    # ── MEXC: perp = BASE_QUOTE, spot = BASEQUOTE. Match by stripping the '_'
    #    from the perp symbol → spot lookup key (avoids splitting a sep-less spot).
    for sym, m_p in mp.items():
        spot_key = sym.replace("_", "")
        m_s = ms.get(spot_key)
        if m_s is None:
            continue                                  # no matching spot pair
        perp_mark = _f(m_p.get("fairPrice"))
        spot_bid = _f(m_s.get("bidPrice"))
        spot_ask = _f(m_s.get("askPrice"))
        spot_price = _mid(spot_bid, spot_ask)
        if perp_mark is None or spot_price is None:
            continue                                  # dead/unpriced symbol — skip
        sv = msv.get(spot_key, {})
        rows.append(_row(
            "mexc", sym, perp_mark, spot_price, _f(m_p.get("fundingRate")),
            _f(m_p.get("bid1")), _f(m_p.get("ask1")), spot_bid, spot_ask,
            iv_get("mexc", sym), ns_get("mexc", sym), sr_get("mexc", sym),
            _f(m_p.get("volume24")), _f(m_p.get("amount24")), _f(m_p.get("holdVol")),
            _f(sv.get("volume")), _f(sv.get("quoteVolume")),
            None, None,                                # MEXC perp ticker has no L1 sizes
            _f(m_s.get("bidQty")), _f(m_s.get("askQty")),
            None,                                      # multiplier not in this response
        ))
        counts["mexc"] += 1

    # ── Gate: perp (contract) and spot (currency_pair) are both BASE_QUOTE.
    for sym, g_p in gp.items():
        g_s = gs.get(sym)
        if g_s is None:
            continue
        perp_mark = _f(g_p.get("mark_price"))
        spot_bid = _f(g_s.get("highest_bid"))
        spot_ask = _f(g_s.get("lowest_ask"))
        spot_price = _mid(spot_bid, spot_ask)
        if perp_mark is None or spot_price is None:
            continue
        rows.append(_row(
            "gate", sym, perp_mark, spot_price, _f(g_p.get("funding_rate")),
            _f(g_p.get("highest_bid")), _f(g_p.get("lowest_ask")), spot_bid, spot_ask,
            iv_get("gate", sym), ns_get("gate", sym), sr_get("gate", sym),
            _f(g_p.get("volume_24h_base")), _f(g_p.get("volume_24h_settle")),
            _f(g_p.get("total_size")),
            _f(g_s.get("base_volume")), _f(g_s.get("quote_volume")),
            _f(g_p.get("highest_size")), _f(g_p.get("lowest_size")),
            None, None,                                # Gate spot ticker has no L1 sizes
            _f(g_p.get("quanto_multiplier")),
        ))
        counts["gate"] += 1

    return rows, counts


# ── DB ───────────────────────────────────────────────────────────────────────
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS funding_basis_snapshots (
    id                     BIGSERIAL PRIMARY KEY,
    ts                     TIMESTAMPTZ      DEFAULT now(),
    exchange               TEXT,
    symbol                 TEXT,
    perp_mark              DOUBLE PRECISION,
    spot_price             DOUBLE PRECISION,
    basis_bps              DOUBLE PRECISION,
    funding_rate           DOUBLE PRECISION,
    funding_interval_hours INTEGER,
    mins_to_funding        DOUBLE PRECISION,
    funding_annualized_pct DOUBLE PRECISION,
    perp_bid               DOUBLE PRECISION,
    perp_ask               DOUBLE PRECISION,
    perp_spread_bps        DOUBLE PRECISION,
    spot_bid               DOUBLE PRECISION,
    spot_ask               DOUBLE PRECISION,
    spot_spread_bps        DOUBLE PRECISION,
    perp_depth5_usd        DOUBLE PRECISION,
    spot_depth5_usd        DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_fbs_ts     ON funding_basis_snapshots (ts DESC);
CREATE INDEX IF NOT EXISTS idx_fbs_symbol ON funding_basis_snapshots (symbol, exchange);

-- Added 2026-08-19. All NULLABLE with no DEFAULT => metadata-only on PG11+,
-- so no table rewrite and no long lock on the 7.9M-row table. History keeps
-- NULL, which correctly reads as "not collected then" rather than a backfill.
ALTER TABLE funding_basis_snapshots
    ADD COLUMN IF NOT EXISTS next_settle_time    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS interval_source     TEXT,
    ADD COLUMN IF NOT EXISTS perp_volume24_base  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS perp_volume24_usd   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS perp_open_interest  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS spot_volume24_base  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS spot_volume24_usd   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS perp_bid_size       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS perp_ask_size       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS spot_bid_size       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS spot_ask_size       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS contract_multiplier DOUBLE PRECISION;

-- Collector heartbeat (added 2026-08-23). One row per cycle: what was written,
-- what failed, how long since the last successful write. A stall is now a
-- QUERYABLE FACT, not something you infer from a hole in the data.
CREATE TABLE IF NOT EXISTS carry_collector_health (
    id                    BIGSERIAL PRIMARY KEY,
    ts                    TIMESTAMPTZ DEFAULT now(),
    cycle                 BIGINT,
    rows_written          INTEGER,
    mexc_rows             INTEGER,
    gate_rows             INTEGER,
    failed_endpoints      TEXT,
    secs_since_last_write DOUBLE PRECISION,
    consecutive_failures  INTEGER,
    action                TEXT          -- 'ok' | 'partial' | 'soft-stall' | 'hard-stall'
);
CREATE INDEX IF NOT EXISTS idx_cch_ts ON carry_collector_health (ts DESC);

CREATE TABLE IF NOT EXISTS carry_funding_intervals (
    exchange       TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    interval_hours DOUBLE PRECISION,
    source         TEXT,
    fetched_ts     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (exchange, symbol)
);

-- Read-time correction for HISTORY. Raw stored data is never rewritten.
-- INNER JOIN: a symbol with no measured interval DROPS OUT rather than being
-- silently annualised at a default.
CREATE OR REPLACE VIEW v_carry_corrected AS
SELECT f.*,
       i.interval_hours AS true_interval_hours,
       f.funding_rate * (24.0 / i.interval_hours) * 365 * 100
           AS funding_apr_corrected_pct
FROM funding_basis_snapshots f
JOIN carry_funding_intervals i
  ON i.exchange = f.exchange AND i.symbol = f.symbol;
"""

INSERT_SQL = """
INSERT INTO funding_basis_snapshots
    (exchange, symbol, perp_mark, spot_price, basis_bps, funding_rate,
     funding_interval_hours, mins_to_funding, funding_annualized_pct,
     perp_bid, perp_ask, perp_spread_bps, spot_bid, spot_ask, spot_spread_bps,
     perp_depth5_usd, spot_depth5_usd,
     next_settle_time, interval_source,
     perp_volume24_base, perp_volume24_usd, perp_open_interest,
     spot_volume24_base, spot_volume24_usd,
     perp_bid_size, perp_ask_size, spot_bid_size, spot_ask_size,
     contract_multiplier)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
        $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29)
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as con:
        await con.execute(CREATE_SQL)


async def write_health(pool: asyncpg.Pool, **kw) -> None:
    """Heartbeat. Best-effort: a health-write failure must never mask the real
    error that produced it, so it warns and returns."""
    try:
        await pool.execute(
            """INSERT INTO carry_collector_health
                   (cycle, rows_written, mexc_rows, gate_rows, failed_endpoints,
                    secs_since_last_write, consecutive_failures, action)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            kw.get("cycle"), kw.get("rows_written"), kw.get("mexc_rows"),
            kw.get("gate_rows"), kw.get("failed_endpoints"),
            kw.get("secs_since_last_write"), kw.get("consecutive_failures"),
            kw.get("action"))
    except Exception as exc:                          # noqa: BLE001
        log.warning("[carry] health write failed: %r", exc)


async def insert_rows(pool: asyncpg.Pool, rows: list[tuple]) -> int:
    if not rows:
        return 0
    async with pool.acquire() as con:
        await con.executemany(INSERT_SQL, rows)
    return len(rows)


# ── Main loop ────────────────────────────────────────────────────────────────
class Stalled(RuntimeError):
    """Hard stall — nothing written for STALL_HARD_SECS. Raised so the process
    EXITS and systemd restarts it clean, rather than looping on a dead session."""


async def _cycle(session: aiohttp.ClientSession, pool: asyncpg.Pool,
                 ivmap: IntervalMap, t0: float,
                 vol_cache: dict) -> tuple[int, dict, list[FetchError]]:
    """One collection cycle. Returns (rows_written, per-venue counts, failures).

    Raises only on a genuinely unrecoverable error (DB down). Endpoint failures
    are RETURNED, not raised, so a partial cycle still writes what it has.
    """
    if ivmap.due(t0):
        got = await ivmap.refresh(session, t0)
        if got:
            await ivmap.persist(pool)
            log.info("[carry] interval map refreshed: %d symbols", got)

    mp, ms, gp, gs, failures = await fetch_all(session)

    # Volume is polled every Nth cycle and cached; None means "call failed",
    # which keeps the last good map instead of nulling volumes for a cycle.
    if vol_cache["cycle"] % VOLUME_EVERY_N == 0:
        fresh = await fetch_mexc_spot_volume(session)
        if fresh is not None:
            vol_cache["data"] = fresh
    msv = vol_cache["data"]

    rows, counts = build_rows(mp, ms, gp, gs, ivmap=ivmap, mexc_spot_vol=msv)
    n = await insert_rows(pool, rows)
    n_iv = sum(1 for r in rows if r[6] is not None)
    n_vol = sum(1 for r in rows if r[20] is not None)
    log.info("[carry] Inserted %d rows (mexc=%d, gate=%d) | interval=%d "
             "perp_vol=%d spot_vol_mexc=%d%s",
             n, counts["mexc"], counts["gate"], n_iv, n_vol, len(msv),
             ("  DEGRADED: " + ", ".join(f.url for f in failures)) if failures else "")
    return n, counts, failures


async def run() -> None:
    if not DSN:
        raise RuntimeError("NEON_DATABASE_URL not set (check researcher/.env)")

    pool = await asyncpg.create_pool(
        dsn=DSN, min_size=1, max_size=3,
        command_timeout=20.0, statement_cache_size=0,  # Neon/pgBouncer safe
    )
    await ensure_schema(pool)
    ivmap = IntervalMap()
    log.info("[carry] connected; table ready; dynamic universe; cycle=%ds; "
             "interval refresh=%.1fh; watchdog soft=%.0fs hard=%.0fs",
             CYCLE_SECONDS, INTERVAL_REFRESH_HOURS, STALL_SOFT_SECS, STALL_HARD_SECS)

    loop = asyncio.get_event_loop()
    session = aiohttp.ClientSession()
    last_write = loop.time()          # monotonic time of the last SUCCESSFUL insert
    consecutive_failures = 0
    vol_cache: dict = {"cycle": 0, "data": {}}
    cycle = 0

    try:
        while True:
            cycle += 1
            vol_cache["cycle"] = cycle
            t0 = loop.time()
            n, counts, failures = 0, {"mexc": 0, "gate": 0}, []
            action = "ok"

            try:
                n, counts, failures = await _cycle(session, pool, ivmap, t0, vol_cache)
                if n:
                    last_write = loop.time()
                    consecutive_failures = 0
                    action = "partial" if failures else "ok"
                else:
                    consecutive_failures += 1
                    action = "partial"
                    log.error("[carry] cycle %d wrote 0 rows (failed: %s)", cycle,
                              ", ".join(f.url for f in failures) or "none")
            except Exception as exc:                  # noqa: BLE001 — loud, counted, escalated
                consecutive_failures += 1
                action = "error"
                # Loud and specific: the old bare handler logged an anonymous
                # traceback per cycle and nothing ever escalated.
                log.exception("[carry] cycle %d FAILED (%d in a row): %r",
                              cycle, consecutive_failures, exc)

            # ── watchdog ────────────────────────────────────────────────────
            stalled_for = loop.time() - last_write
            if stalled_for > STALL_HARD_SECS:
                action = "hard-stall"
                await write_health(
                    pool, cycle=cycle, rows_written=n, mexc_rows=counts["mexc"],
                    gate_rows=counts["gate"],
                    failed_endpoints=", ".join(f.url for f in failures) or None,
                    secs_since_last_write=stalled_for,
                    consecutive_failures=consecutive_failures, action=action)
                log.error("[carry] HARD STALL: no successful write for %.0fs "
                          "(limit %.0fs, %d consecutive failures) — EXITING for "
                          "a clean systemd restart", stalled_for, STALL_HARD_SECS,
                          consecutive_failures)
                raise Stalled(f"no write for {stalled_for:.0f}s")

            if stalled_for > STALL_SOFT_SECS:
                action = "soft-stall"
                log.error("[carry] SOFT STALL: no successful write for %.0fs "
                          "(limit %.0fs) — rebuilding HTTP session "
                          "(fresh connector + DNS)", stalled_for, STALL_SOFT_SECS)
                try:
                    await session.close()
                except Exception as exc:              # noqa: BLE001
                    log.warning("[carry] session close failed: %r", exc)
                session = aiohttp.ClientSession()

            await write_health(
                pool, cycle=cycle, rows_written=n, mexc_rows=counts["mexc"],
                gate_rows=counts["gate"],
                failed_endpoints=", ".join(f.url for f in failures) or None,
                secs_since_last_write=stalled_for,
                consecutive_failures=consecutive_failures, action=action)

            elapsed = loop.time() - t0
            await asyncio.sleep(max(1.0, CYCLE_SECONDS - elapsed))
    finally:
        await session.close()
        await pool.close()


if __name__ == "__main__":
    import sys
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("[carry] stopped by user")
    except Stalled as exc:
        # Non-zero exit: systemd Restart=always gives us a clean process.
        log.error("[carry] exiting on watchdog: %s", exc)
        sys.exit(1)
