"""Shared plumbing for the DATED-FUTURES BASIS collector.

READ-ONLY, PUBLIC ENDPOINTS ONLY. No keys, no orders, no private call path
anywhere in this package.

WHAT THIS MEASURES — and why it is not the carry collector
    Cash-and-carry basis: buy spot, sell a DATED (delivery) future trading at a
    premium. The premium converges to zero at a FIXED expiry, so unlike perp
    funding the horizon is known in advance rather than re-priced every epoch.

        annualized_pct = (future - spot) / spot * (365 / days_to_expiry)

    PERPETUALS ARE EXCLUDED EVERYWHERE. This is the dated term structure only;
    perp funding already has its own collectors and its own tables.

ISOLATION (hard requirement)
    Writes `basis_snapshots` and `basis_collector_health` and NOTHING else.
    It does not read or write funding_basis_snapshots, venue_funding_snapshots,
    bybit_funding_snapshots, carry_book_l2, paper_carry_* or any ёрш table. The
    paper bot's selector cannot see this data, which is deliberate: there is no
    execution path for dated futures and a selector that could see them would
    try to "trade" them.

EXPIRY IS MEASURED, NEVER ASSUMED. Every adapter reads the expiry off the
instrument definition and records WHICH field it came from (`expiry_source`).
Annualising on a guessed expiry is the same class of bug as the 8h funding
hardcode that mispriced 95 of 129 carry names by 2x.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

log = logging.getLogger("basis")

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)
FETCH_RETRIES = 3
FETCH_BACKOFF_SECS = (1.0, 4.0, 10.0)

# A future/spot PRICE ratio outside this band is a unit mismatch, not a basis:
# a 1000x-style listing or a coin-margined contract quoted per-contract instead
# of per-coin. Dropped and COUNTED, never written.
#
# The band is deliberately wider than the perp collector's 0.9-1.1: a genuine
# 1-year dated future can legitimately sit well away from spot, and clipping
# real contango would destroy the signal we are here to measure. +/-25-35%
# still catches every unit error, which are order-of-magnitude, not percent.
MIN_PX_RATIO = 0.75
MAX_PX_RATIO = 1.35

# Anything past this is not a dated future we care about — it is a nominal-expiry
# perp dressed as a future (OKX 'this_five_years' XPERP is exactly this).
MAX_DAYS_TO_EXPIRY = 400.0
# Below this, 365/days explodes and the annualised number is noise, so it is
# left NULL and counted. The raw basis_bps is still recorded.
MIN_DAYS_FOR_ANNUAL = 0.5


def f(v: Any) -> Optional[float]:
    """Parse to float, None on missing/blank/unparseable — never fabricate."""
    if v is None or v == "":
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out


def pos(v: Any) -> Optional[float]:
    """As f(), but a non-positive price is missing data, not a price."""
    out = f(v)
    return out if out is not None and out > 0 else None


def mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def spread_bps(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    m = mid(bid, ask)
    if m is None:
        return None
    return (ask - bid) / m * 10000.0


def ms_to_dt(v: Any) -> Optional[datetime]:
    n = f(v)
    if n is None or n <= 0:
        return None
    return datetime.fromtimestamp(n / 1000.0, tz=timezone.utc)


def s_to_dt(v: Any) -> Optional[datetime]:
    n = f(v)
    if n is None or n <= 0:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc)


def px(bid: Optional[float], ask: Optional[float],
       *fallbacks: tuple[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """Return (price, source). Prefer the book mid; fall back only to what the
    venue actually published, and SAY which one was used.

    Near expiry a book goes one-sided and mid() is undefined. Falling back to
    mark/last is right, but silently mixing price bases makes a later
    "why is this basis odd" question undecidable — hence the label.
    """
    m = mid(bid, ask)
    if m is not None:
        return m, "mid"
    for name, v in fallbacks:
        p = pos(v)
        if p is not None:
            return p, name
    return None, None


class FetchError(Exception):
    """One endpoint failed every retry. Names the URL so a degraded cycle says
    WHICH feed is down instead of logging an anonymous traceback."""

    def __init__(self, url: str, cause: BaseException) -> None:
        super().__init__(f"{url}: {cause!r}")
        self.url, self.cause = url, cause


async def get_json(session: aiohttp.ClientSession, url: str,
                   retries: int | None = None) -> Any:
    """Fetch with backoff; raise FetchError on give-up.

    Never returns a sentinel. An empty list reads downstream as "the venue
    lists no dated futures", which is how a hole gets written into history as
    if it were data.
    """
    attempts = FETCH_RETRIES if retries is None else retries
    last: BaseException | None = None
    for i in range(attempts):
        try:
            async with session.get(url, timeout=HTTP_TIMEOUT) as r:
                r.raise_for_status()
                return await r.json(content_type=None)
        except Exception as exc:                      # noqa: BLE001 — re-raised below
            last = exc
            if i + 1 < attempts:
                await asyncio.sleep(FETCH_BACKOFF_SECS[min(i, len(FETCH_BACKOFF_SECS) - 1)])
    raise FetchError(url, last) from last


async def gather_isolated(session: aiohttp.ClientSession,
                          urls: list[str]) -> tuple[list, list]:
    """Fetch several endpoints INDEPENDENTLY.

    One dead feed must not discard the cycle — that single mistake already cost
    ~40h of MEXC/Gate funding history. Returns (results, failures) with a
    failed feed as None.
    """
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


# Column order here IS the INSERT order. Both live in this module so they
# cannot drift apart.
COLUMNS = (
    "exchange", "coin", "future_symbol", "spot_symbol",
    "contract_type", "settle_ccy", "cycle_label",
    "expiry_ts", "expiry_source", "days_to_expiry",
    "spot_price", "spot_bid", "spot_ask", "spot_spread_bps", "spot_source",
    "spot_px_source",
    "future_price", "future_bid", "future_ask", "future_spread_bps", "future_mark",
    "future_px_source",
    "px_ratio", "basis_bps", "annualized_pct", "roundtrip_spread_bps",
    "venue_basis_raw", "venue_basis_field",
    "future_oi", "future_vol24_usd", "spot_vol24_usd", "contract_multiplier",
)


class Row:
    """One dated (coin, venue, expiry) observation.

    Derived fields are computed here rather than by each adapter, so every
    venue's basis is arithmetically identical and a venue bug cannot hide in a
    bespoke formula.
    """

    __slots__ = ("exchange", "coin", "future_symbol", "spot_symbol",
                 "contract_type", "settle_ccy", "cycle_label",
                 "expiry_ts", "expiry_source",
                 "spot_price", "spot_bid", "spot_ask", "spot_source", "spot_px_source",
                 "future_price", "future_bid", "future_ask", "future_mark",
                 "future_px_source",
                 "venue_basis_raw", "venue_basis_field",
                 "future_oi", "future_vol24_usd", "spot_vol24_usd",
                 "contract_multiplier", "now")

    def __init__(self, **kw) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        if self.now is None:
            self.now = datetime.now(timezone.utc)

    # -- derived ---------------------------------------------------------
    @property
    def days_to_expiry(self) -> Optional[float]:
        if self.expiry_ts is None:
            return None
        return (self.expiry_ts - self.now).total_seconds() / 86400.0

    @property
    def px_ratio(self) -> Optional[float]:
        if not self.future_price or not self.spot_price or self.spot_price <= 0:
            return None
        return self.future_price / self.spot_price

    @property
    def basis_bps(self) -> Optional[float]:
        r = self.px_ratio
        return None if r is None else (r - 1.0) * 10000.0

    @property
    def annualized_pct(self) -> Optional[float]:
        b, d = self.basis_bps, self.days_to_expiry
        if b is None or d is None or d < MIN_DAYS_FOR_ANNUAL:
            return None
        return (b / 100.0) * (365.0 / d)

    @property
    def roundtrip_spread_bps(self) -> Optional[float]:
        """Both legs must cross, so the executability cost is the SUM. None when
        either leg has no book — a half-measured cost reads as a cheap trade."""
        a = spread_bps(self.spot_bid, self.spot_ask)
        b = spread_bps(self.future_bid, self.future_ask)
        if a is None or b is None:
            return None
        return a + b

    def as_tuple(self) -> tuple:
        return (
            self.exchange, self.coin, self.future_symbol, self.spot_symbol,
            self.contract_type, self.settle_ccy, self.cycle_label,
            self.expiry_ts, self.expiry_source, self.days_to_expiry,
            self.spot_price, self.spot_bid, self.spot_ask,
            spread_bps(self.spot_bid, self.spot_ask), self.spot_source,
            self.spot_px_source,
            self.future_price, self.future_bid, self.future_ask,
            spread_bps(self.future_bid, self.future_ask), self.future_mark,
            self.future_px_source,
            self.px_ratio, self.basis_bps, self.annualized_pct,
            self.roundtrip_spread_bps,
            self.venue_basis_raw, self.venue_basis_field,
            self.future_oi, self.future_vol24_usd, self.spot_vol24_usd,
            self.contract_multiplier,
        )


def screen(row: Row, stats: dict) -> bool:
    """Gate a row before it is written. Every rejection is COUNTED so a silent
    universe collapse shows up in the health table instead of looking like a
    venue that stopped listing futures."""
    if row.future_price is None or row.spot_price is None:
        stats["unpriced"] = stats.get("unpriced", 0) + 1
        return False
    if row.expiry_ts is None:
        stats["no_expiry"] = stats.get("no_expiry", 0) + 1
        return False
    d = row.days_to_expiry
    if d is None or d <= 0:
        stats["expired"] = stats.get("expired", 0) + 1
        return False
    if d > MAX_DAYS_TO_EXPIRY:
        stats["too_far"] = stats.get("too_far", 0) + 1
        return False
    r = row.px_ratio
    if r is None or not (MIN_PX_RATIO <= r <= MAX_PX_RATIO):
        stats["unit_skip"] = stats.get("unit_skip", 0) + 1
        log.warning("[basis/%s] UNIT SKIP %s: future=%s spot=%s ratio=%s",
                    row.exchange, row.future_symbol, row.future_price,
                    row.spot_price, None if r is None else round(r, 4))
        return False
    if row.annualized_pct is None:
        stats["no_annual"] = stats.get("no_annual", 0) + 1   # written, not dropped
    return True
