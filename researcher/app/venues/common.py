"""Shared plumbing for the candidate-venue funding collectors.

READ-ONLY, PUBLIC ENDPOINTS ONLY. No keys, no orders, no private call path.

Every venue adapter returns rows in ONE canonical shape (see `Snapshot`), so the
cross-venue comparison on 2026-08-26 is a single GROUP BY rather than four
bespoke queries.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

log = logging.getLogger("venues")

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)
FETCH_RETRIES = 3
FETCH_BACKOFF_SECS = (1.0, 4.0, 10.0)

# A pair whose perp and spot disagree on units by more than this is DROPPED and
# COUNTED, never written. Without it a 1000x-style listing shows up as a
# 10,000 bps "basis" that looks like a spectacular carry opportunity.
MIN_PX_RATIO = 0.9
MAX_PX_RATIO = 1.1


def f(v: Any) -> Optional[float]:
    """Parse to float, None on missing/blank/unparseable — never fabricate."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def spread_bps(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    m = mid(bid, ask)
    if m is None or ask is None or bid is None:
        return None
    return (ask - bid) / m * 10000.0


def basis_bps(perp_mark: Optional[float], spot: Optional[float]) -> Optional[float]:
    if perp_mark is None or spot is None or spot <= 0:
        return None
    return (perp_mark - spot) / spot * 10000.0


def apr_pct(rate: Optional[float], interval_h: Optional[float]) -> Optional[float]:
    """Annualise on the symbol's REAL interval. None when unknown.

    Never defaults to 8h. OKX/Bitget/KuCoin all mix 4h and 8h (and a stray 1h),
    so a hardcode would misprice the majority of every one of these venues by
    2x — the bug that already cost us once on MEXC/Gate.
    """
    if rate is None or not interval_h or interval_h <= 0:
        return None
    return rate * (24.0 / interval_h) * 365.0 * 100.0


def mins_until(ts: Optional[datetime]) -> Optional[float]:
    if ts is None:
        return None
    return (ts - datetime.now(timezone.utc)).total_seconds() / 60.0


class FetchError(Exception):
    """One endpoint failed every retry. Names the URL so a degraded cycle says
    WHICH feed is down instead of logging an anonymous traceback."""

    def __init__(self, url: str, cause: BaseException) -> None:
        super().__init__(f"{url}: {cause!r}")
        self.url, self.cause = url, cause


async def get_json(session: aiohttp.ClientSession, url: str,
                   retries: int | None = None) -> Any:
    """Fetch with backoff; raise FetchError on give-up.

    Never returns a sentinel — an empty list reads downstream as "the venue
    listed nothing", which is how a hole gets written into history as if it
    were data.
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


async def gather_isolated(session: aiohttp.ClientSession, urls: list[str]) -> tuple:
    """Fetch several endpoints INDEPENDENTLY.

    One dead feed must not discard the cycle — that single mistake cost ~40h of
    MEXC/Gate funding history. Returns (results, failures) with a failed feed
    as None.
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


class Row:
    """One canonical snapshot row. Column order matches INSERT_SQL exactly."""

    __slots__ = ("exchange", "symbol", "perp_mark", "spot_price", "funding_rate",
                 "interval_h", "next_settle", "interval_source",
                 "perp_bid", "perp_ask", "spot_bid", "spot_ask",
                 "perp_vol_base", "perp_vol_usd", "perp_oi",
                 "spot_vol_base", "spot_vol_usd",
                 "perp_bid_size", "perp_ask_size", "spot_bid_size", "spot_ask_size",
                 "contract_multiplier", "perp_index_price")

    def __init__(self, **kw) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def as_tuple(self) -> tuple:
        return (
            self.exchange, self.symbol, self.perp_mark, self.spot_price,
            basis_bps(self.perp_mark, self.spot_price), self.funding_rate,
            self.interval_h, mins_until(self.next_settle),
            apr_pct(self.funding_rate, self.interval_h),
            self.perp_bid, self.perp_ask, spread_bps(self.perp_bid, self.perp_ask),
            self.spot_bid, self.spot_ask, spread_bps(self.spot_bid, self.spot_ask),
            None, None,                       # depth5 not collected here
            self.next_settle, self.interval_source,
            self.perp_vol_base, self.perp_vol_usd, self.perp_oi,
            self.spot_vol_base, self.spot_vol_usd,
            self.perp_bid_size, self.perp_ask_size,
            self.spot_bid_size, self.spot_ask_size,
            self.contract_multiplier, self.perp_index_price,
        )


def unit_gate(perp_mark: Optional[float], spot_price: Optional[float]) -> bool:
    """True when the two legs agree on units closely enough to trust the basis."""
    if not perp_mark or not spot_price or spot_price <= 0:
        return False
    return MIN_PX_RATIO <= (perp_mark / spot_price) <= MAX_PX_RATIO
