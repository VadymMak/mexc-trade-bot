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
async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url, timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        return await r.json()


async def fetch_all(session: aiohttp.ClientSession) -> tuple:
    return await asyncio.gather(
        _get_json(session, MEXC_PERP_URL),
        _get_json(session, MEXC_SPOT_URL),
        _get_json(session, GATE_PERP_URL),
        _get_json(session, GATE_SPOT_URL),
    )


async def fetch_mexc_spot_volume(session: aiohttp.ClientSession) -> dict:
    """MEXC spot bookTicker carries no volume, so pull it separately.
    BEST EFFORT: a failure yields {} (volumes NULL) and never affects the
    universe or fails the cycle."""
    try:
        d = await _get_json(session, MEXC_SPOT_24H_URL)
        return {x["symbol"]: x for x in d if x.get("symbol")}
    except Exception as exc:
        log.warning("[carry] mexc spot 24h volume fetch failed: %r (volumes NULL)", exc)
        return {}


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
    mp = {d["symbol"]: d for d in mexc_perp.get("data", [])}   # perp: BTC_USDT
    ms = {d["symbol"]: d for d in mexc_spot}                    # spot: BTCUSDT (no sep)
    gp = {d["contract"]: d for d in gate_perp}                  # perp: BTC_USDT
    gs = {d["currency_pair"]: d for d in gate_spot}            # spot: BTC_USDT
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


async def insert_rows(pool: asyncpg.Pool, rows: list[tuple]) -> int:
    if not rows:
        return 0
    async with pool.acquire() as con:
        await con.executemany(INSERT_SQL, rows)
    return len(rows)


# ── Main loop ────────────────────────────────────────────────────────────────
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
             "interval refresh=%.1fh", CYCLE_SECONDS, INTERVAL_REFRESH_HOURS)

    async with aiohttp.ClientSession() as session:
        while True:
            t0 = asyncio.get_event_loop().time()
            try:
                if ivmap.due(t0):
                    got = await ivmap.refresh(session, t0)
                    if got:
                        await ivmap.persist(pool)
                        log.info("[carry] interval map refreshed: %d symbols", got)

                mp, ms, gp, gs = await fetch_all(session)
                msv = await fetch_mexc_spot_volume(session)
                rows, counts = build_rows(mp, ms, gp, gs, ivmap=ivmap, mexc_spot_vol=msv)
                n = await insert_rows(pool, rows)
                n_iv = sum(1 for r in rows if r[6] is not None)
                n_vol = sum(1 for r in rows if r[20] is not None)
                log.info("[carry] Inserted %d rows (mexc=%d, gate=%d) | interval=%d "
                         "perp_vol=%d spot_vol_mexc=%d",
                         n, counts["mexc"], counts["gate"], n_iv, n_vol, len(msv))
            except Exception as e:
                log.exception("[carry] cycle error: %s", e)
            elapsed = asyncio.get_event_loop().time() - t0
            await asyncio.sleep(max(1.0, CYCLE_SECONDS - elapsed))


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("[carry] stopped by user")
