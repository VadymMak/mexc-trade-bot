"""Shared plumbing for the STABLECOIN LENDING / BORROW rate collector.

READ-ONLY, PUBLIC ENDPOINTS ONLY. No keys, no orders, no private call path.

WHAT THIS MEASURES — and why it is a third, separate thing
    The supply (lend) APY and the borrow rate for USDT and USDC. This is the
    price of stablecoin credit in the margin/lending market, which is NOT the
    same signal as perp funding (price of leverage in the swap) or dated basis
    (price of term structure). Collecting all three over the same calendar lets
    us ask whether they move together or apart — that correlation is the actual
    portfolio question, and it cannot be answered from one snapshot.

NORMALISATION IS THE WHOLE GAME HERE
    Every venue quotes on a different clock and NONE of them label it in the
    payload. Getting this wrong is not a rounding error — reading OKX's daily
    rate as hourly turns 3.0% into 72.1%. So each row stores four things:
        raw_rate            what the venue actually published
        raw_basis           the clock we believe it is on
        conversion_factor   the exact multiplier applied
        rate_field          the upstream field name it came from
    annual_pct is then reproducible from the raw value by anyone who disagrees
    with the assumption, instead of being an unverifiable derived number.

    Conventions established by probe on 2026-08-24, each corroborated:
      okx    supply  estRate            annual fraction  (3.5% USDT)
      okx    borrow  basic[].rate       DAILY  — x365 = 3.005%, which matches
                                        OKX's own savings preRate of 0.0300 to
                                        four decimals; read as hourly it would
                                        be 72.1%, which is absurd for USDT
      bybit  borrow  hourlyBorrowRate   HOURLY — the field says so
      kucoin supply  marketInterestRate DAILY  — x365 = 3.65%; hourly would be
                                        87.6%
      binance borrow dailyInterestRate  DAILY  — the field says so
      aave   both    apyBase/apyBaseBorrow  already annual PERCENT

SANITY GATE. A stablecoin lending rate outside [-5%, +200%] annual is a
normalisation or units failure, not a market. Dropped, logged and COUNTED —
never written as if it were an observation.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

log = logging.getLogger("lending")

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)
FETCH_RETRIES = 3
FETCH_BACKOFF_SECS = (1.0, 4.0, 10.0)

ASSETS = ("USDT", "USDC")

MIN_ANNUAL_PCT = -5.0
MAX_ANNUAL_PCT = 200.0

# raw_basis -> multiplier that takes the raw value to an annual PERCENT.
BASES: dict[str, float] = {
    "annual_pct": 1.0,
    "annual_fraction": 100.0,
    "daily": 365.0 * 100.0,
    "hourly": 24.0 * 365.0 * 100.0,
}

# Venues checked and found to publish nothing usable without credentials.
# Recorded here (and logged at startup) so the dataset documents its own gaps
# rather than leaving a future reader to wonder whether they were forgotten.
AUTH_SKIPPED = (
    ("gate", "margin/uni/estimate_rate needs a signed Timestamp header; "
             "earn/uni/currencies is public but exposes only configured "
             "min_rate/max_rate BOUNDS, not a live rate — bounds are not an "
             "observation, so nothing is written"),
    ("bitget", "margin/currencies carries leverage and fees but no interest "
               "rate; earn/savings/product and margin/crossed/"
               "interest-rate-and-limit both answer 'Invalid ACCESS_KEY'"),
    ("binance-supply", "sapi/v1/margin/interestRateHistory and crossMarginData "
                       "require an API key; the public earn product list 404s. "
                       "Binance BORROW is collected from a public endpoint; "
                       "Binance SUPPLY is unavailable"),
    ("kucoin-borrow", "api/v3/margin/currencies requires KC-API-KEY. KuCoin "
                      "SUPPLY is public; KuCoin BORROW is unavailable"),
)


def f(v: Any) -> Optional[float]:
    """Parse to float, None on missing/blank/unparseable — never fabricate."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_annual_pct(raw: Optional[float], basis: str) -> tuple[Optional[float], Optional[float]]:
    """Return (annual_pct, conversion_factor). Unknown basis is a bug, not a
    number: it returns None rather than silently passing the raw value through."""
    if raw is None:
        return None, None
    k = BASES.get(basis)
    if k is None:
        log.error("unknown raw_basis %r — refusing to convert", basis)
        return None, None
    return raw * k, k


class FetchError(Exception):
    """One endpoint failed every retry. Names the URL so a degraded cycle says
    WHICH feed is down instead of logging an anonymous traceback."""

    def __init__(self, url: str, cause: BaseException) -> None:
        super().__init__(f"{url}: {cause!r}")
        self.url, self.cause = url, cause


async def get_json(session: aiohttp.ClientSession, url: str,
                   retries: int | None = None) -> Any:
    """Fetch with backoff; raise FetchError on give-up. Never a sentinel — an
    empty payload reads downstream as 'the venue quotes nothing', which is how
    a hole gets written into history as if it were data."""
    attempts = FETCH_RETRIES if retries is None else retries
    last: BaseException | None = None
    for i in range(attempts):
        try:
            async with session.get(url, timeout=HTTP_TIMEOUT,
                                   headers={"User-Agent": "Mozilla/5.0"}) as r:
                r.raise_for_status()
                return await r.json(content_type=None)
        except Exception as exc:                      # noqa: BLE001 — re-raised below
            last = exc
            if i + 1 < attempts:
                await asyncio.sleep(FETCH_BACKOFF_SECS[min(i, len(FETCH_BACKOFF_SECS) - 1)])
    raise FetchError(url, last) from last


async def gather_isolated(session: aiohttp.ClientSession,
                          urls: list[str]) -> tuple[list, list]:
    """Fetch several endpoints INDEPENDENTLY. One dead feed must not discard the
    cycle. Returns (results, failures) with a failed feed as None."""
    res = await asyncio.gather(*(get_json(session, u) for u in urls),
                              return_exceptions=True)
    out, failures = [], []
    for url, r in zip(urls, res):
        if isinstance(r, BaseException):
            out.append(None)
            failures.append(r if isinstance(r, FetchError) else FetchError(url, r))
        else:
            out.append(r)
    return out, failures


# Column order here IS the INSERT order; both live in this module so they cannot
# drift apart.
COLUMNS = ("source", "venue_kind", "asset", "rate_type", "annual_pct",
           "raw_rate", "raw_basis", "conversion_factor", "rate_field",
           "tier", "term", "endpoint", "observed_at", "extra")


class Row:
    __slots__ = ("source", "venue_kind", "asset", "rate_type", "raw_rate",
                 "raw_basis", "rate_field", "tier", "term", "endpoint",
                 "observed_at", "extra")

    def __init__(self, **kw) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def converted(self) -> tuple[Optional[float], Optional[float]]:
        return to_annual_pct(self.raw_rate, self.raw_basis or "")

    def as_tuple(self) -> tuple:
        ann, factor = self.converted
        return (self.source, self.venue_kind, self.asset, self.rate_type, ann,
                self.raw_rate, self.raw_basis, factor, self.rate_field,
                self.tier, self.term, self.endpoint, self.observed_at,
                self.extra)


def screen(row: Row, stats: dict) -> bool:
    """Gate a row before it is written. Every rejection is COUNTED so a source
    that quietly changed its units shows up in the health table instead of
    looking like a venue that stopped quoting."""
    if row.raw_rate is None:
        stats["missing"] = stats.get("missing", 0) + 1
        return False
    ann, _ = row.converted
    if ann is None:
        stats["no_conversion"] = stats.get("no_conversion", 0) + 1
        return False
    if not (MIN_ANNUAL_PCT <= ann <= MAX_ANNUAL_PCT):
        stats["out_of_range"] = stats.get("out_of_range", 0) + 1
        log.warning("[lending/%s] RANGE SKIP %s %s: raw=%s basis=%s -> %.3f%% "
                    "outside [%.0f, %.0f] — units may have changed upstream",
                    row.source, row.asset, row.rate_type, row.raw_rate,
                    row.raw_basis, ann, MIN_ANNUAL_PCT, MAX_ANNUAL_PCT)
        return False
    return True


def ts_from_compact(s: str) -> Optional[datetime]:
    """KuCoin publishes 'YYYYMMDDHHMM' with no zone; read as UTC."""
    try:
        return datetime.strptime(s, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
