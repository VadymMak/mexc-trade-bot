"""
Carry (funding / basis) data collector — ADDITIVE, data collection only.

Every CYCLE_SECONDS:
  - 4 bulk REST calls (MEXC perp+spot, Gate perp+spot)
  - join per (exchange, symbol)
  - compute basis_bps, funding_annualized_pct, perp/spot spread_bps
  - batch-INSERT into funding_basis_snapshots (new table, self-healing CREATE)

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
FUNDING_INTERVAL_HOURS = 8           # both MEXC and Gate settle every 8h (00/08/16 UTC)
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Universe is built DYNAMICALLY each cycle from the bulk responses (below):
# every symbol that appears in BOTH the perp and spot bulk feed on an exchange.
# No hardcoded coin list.

MEXC_PERP_URL = "https://contract.mexc.com/api/v1/contract/ticker"
MEXC_SPOT_URL = "https://api.mexc.com/api/v3/ticker/bookTicker"
GATE_PERP_URL = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
GATE_SPOT_URL = "https://api.gateio.ws/api/v4/spot/tickers"

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


def _funding_apr_pct(funding_rate: Optional[float]) -> Optional[float]:
    if funding_rate is None:
        return None
    return funding_rate * (24.0 / FUNDING_INTERVAL_HOURS) * 365.0 * 100.0


def _mins_to_funding() -> float:
    """Minutes until the next 8h UTC funding boundary (00:00/08:00/16:00 UTC)."""
    now = datetime.now(timezone.utc)
    hours = now.hour + now.minute / 60.0 + now.second / 3600.0
    step = FUNDING_INTERVAL_HOURS
    next_boundary = (int(hours // step) + 1) * step   # 8, 16, or 24
    return (next_boundary - hours) * 60.0


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


# ── Build rows ───────────────────────────────────────────────────────────────
def _row(exchange, sym, perp_mark, spot_price, funding, mins,
         perp_bid, perp_ask, spot_bid, spot_ask) -> tuple:
    return (
        exchange, sym, perp_mark, spot_price,
        _basis_bps(perp_mark, spot_price), funding,
        FUNDING_INTERVAL_HOURS, mins, _funding_apr_pct(funding),
        perp_bid, perp_ask, _spread_bps(perp_bid, perp_ask),
        spot_bid, spot_ask, _spread_bps(spot_bid, spot_ask),
        None, None,               # perp_depth5_usd, spot_depth5_usd (v1: NULL)
    )


def build_rows(mexc_perp, mexc_spot, gate_perp, gate_spot) -> tuple[list[tuple], dict]:
    """
    Dynamic universe: for each exchange, take every symbol present in BOTH the
    perp and spot bulk feed. Insert a row only if it has a numeric spot price
    AND a numeric perp mark. funding_rate may be NULL (never fabricated).
    Returns (rows, per-exchange coin counts) for logging.
    """
    mp = {d["symbol"]: d for d in mexc_perp.get("data", [])}   # perp: BTC_USDT
    ms = {d["symbol"]: d for d in mexc_spot}                    # spot: BTCUSDT (no sep)
    gp = {d["contract"]: d for d in gate_perp}                  # perp: BTC_USDT
    gs = {d["currency_pair"]: d for d in gate_spot}            # spot: BTC_USDT

    mins = _mins_to_funding()
    rows: list[tuple] = []
    counts = {"mexc": 0, "gate": 0}

    # ── MEXC: perp = BASE_QUOTE, spot = BASEQUOTE. Match by stripping the '_'
    #    from the perp symbol → spot lookup key (avoids splitting a sep-less spot).
    for sym, m_p in mp.items():
        m_s = ms.get(sym.replace("_", ""))
        if m_s is None:
            continue                                  # no matching spot pair
        perp_mark = _f(m_p.get("fairPrice"))
        spot_bid = _f(m_s.get("bidPrice"))
        spot_ask = _f(m_s.get("askPrice"))
        spot_price = _mid(spot_bid, spot_ask)
        if perp_mark is None or spot_price is None:
            continue                                  # dead/unpriced symbol — skip
        rows.append(_row(
            "mexc", sym, perp_mark, spot_price, _f(m_p.get("fundingRate")), mins,
            _f(m_p.get("bid1")), _f(m_p.get("ask1")), spot_bid, spot_ask,
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
            "gate", sym, perp_mark, spot_price, _f(g_p.get("funding_rate")), mins,
            _f(g_p.get("highest_bid")), _f(g_p.get("lowest_ask")), spot_bid, spot_ask,
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
"""

INSERT_SQL = """
INSERT INTO funding_basis_snapshots
    (exchange, symbol, perp_mark, spot_price, basis_bps, funding_rate,
     funding_interval_hours, mins_to_funding, funding_annualized_pct,
     perp_bid, perp_ask, perp_spread_bps, spot_bid, spot_ask, spot_spread_bps,
     perp_depth5_usd, spot_depth5_usd)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
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
    log.info("[carry] connected; table ready; dynamic universe; cycle=%ds",
             CYCLE_SECONDS)

    async with aiohttp.ClientSession() as session:
        while True:
            t0 = asyncio.get_event_loop().time()
            try:
                mp, ms, gp, gs = await fetch_all(session)
                rows, counts = build_rows(mp, ms, gp, gs)
                n = await insert_rows(pool, rows)
                log.info("[carry] Inserted %d rows (mexc=%d, gate=%d)",
                         n, counts["mexc"], counts["gate"])
            except Exception as e:
                log.exception("[carry] cycle error: %s", e)
            elapsed = asyncio.get_event_loop().time() - t0
            await asyncio.sleep(max(1.0, CYCLE_SECONDS - elapsed))


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("[carry] stopped by user")
