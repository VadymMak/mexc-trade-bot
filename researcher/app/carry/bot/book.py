"""Executable book pricing from carry_book_l2 — worst-hour aware.

This is the honest-entry-cost layer. It is the same VWAP walk the Phase-2
capacity study used (research/carry_screen/capacity_portfolio.py), lifted into
the bot so paper entries are priced off the real book rather than a mid.

WHY THIS MATTERS: the arbitrage post-mortem showed mark-price simulation at
95.7% win rate and the book-walked truth at 0.3%. Any paper record priced off
mids is worthless as evidence about a live bot.

WORST HOUR: capacity is grouped by hour-of-day (UTC), a median is taken inside
each bucket, and the THINNEST bucket is what sizing uses. The median book
overstates capacity by ~30% typically and by 7x on the worst names.
"""
from __future__ import annotations

import bisect
import datetime as dt
import logging
import statistics
from collections import defaultdict

logger = logging.getLogger(__name__)

# The four legs of a carry round trip and which book side each consumes.
LEGS = (("spot", "ask", "entry_spot_buy"),
        ("perp", "bid", "entry_perp_short"),
        ("spot", "bid", "exit_spot_sell"),
        ("perp", "ask", "exit_perp_cover"))


class Curve:
    """One book side of one snapshot as cumulative arrays.

    f[j]    = |p_j - p_touch| / p_touch, non-decreasing
    cumv[j] = notional USD available through level j
    cumc[j] = sum of v_i * f_i through j (slippage-weighted notional)
    """

    __slots__ = ("f", "cumv", "cumc", "total", "touch")

    def __init__(self, levels: list[tuple[float, float]], side: str) -> None:
        prices = [p for p, _ in levels]
        p1 = max(prices) if side == "bid" else min(prices)
        self.touch = p1
        lv = sorted(((abs(p - p1) / p1, v) for p, v in levels if v and v > 0),
                    key=lambda x: x[0])
        f, cumv, cumc = [], [], []
        sv = sc = 0.0
        for frac, v in lv:
            sv += v
            sc += v * frac
            f.append(frac)
            cumv.append(sv)
            cumc.append(sc)
        self.f, self.cumv, self.cumc, self.total = f, cumv, cumc, sv

    def capacity(self, t_bps: float) -> float:
        """Max USD absorbable with VWAP slippage-from-touch <= t_bps."""
        if not self.f:
            return 0.0
        t = t_bps / 1e4
        k = bisect.bisect_right(self.f, t)
        if k == len(self.f):
            return self.total
        n = self.cumv[k - 1] if k else 0.0
        s = self.cumc[k - 1] if k else 0.0
        vk = self.cumv[k] - (self.cumv[k - 1] if k else 0.0)
        denom = self.f[k] - t
        x = (t * n - s) / denom if denom > 0 else 0.0
        return n + max(0.0, min(x, vk))

    def slip_bps(self, usd: float) -> float | None:
        """VWAP slippage-from-touch in bps for consuming `usd`.
        None when the visible book cannot absorb it."""
        if not self.cumv or self.total < usd or usd <= 0:
            return None
        j = bisect.bisect_left(self.cumv, usd)
        prev_v = self.cumv[j - 1] if j else 0.0
        prev_c = self.cumc[j - 1] if j else 0.0
        return (prev_c + (usd - prev_v) * self.f[j]) / usd * 1e4

    def vwap_price(self, usd: float, side: str) -> float | None:
        """Executable VWAP price for consuming `usd` of this side."""
        s = self.slip_bps(usd)
        if s is None:
            return None
        # buying (ask side) pays up, selling (bid side) receives down
        sign = 1.0 if side == "ask" else -1.0
        return self.touch * (1.0 + sign * s / 1e4)


_LEG_SQL = """
SELECT ts, price, size_usd
FROM carry_book_l2
WHERE exchange=$1 AND symbol=$2 AND market=$3 AND side=$4
      AND ts > now() - ($5 || ' hours')::interval
      AND price > 0 AND size_usd IS NOT NULL AND size_usd > 0
ORDER BY ts, level
"""


class BookSource:
    """Loads and caches per-leg curves grouped by hour-of-day."""

    def __init__(self, pool, cfg) -> None:
        self._pool = pool
        self._cfg = cfg
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._cache_secs = 300.0

    async def leg_curves(self, ex: str, sym: str, market: str, side: str
                         ) -> dict[int, list[Curve]]:
        """-> {hour_of_day: [Curve]} over the depth lookback window."""
        import time as _t
        key = (ex, sym, market, side)
        hit = self._cache.get(key)
        if hit and _t.monotonic() - hit[0] < self._cache_secs:
            return hit[1]
        rows = await self._pool.fetch(_LEG_SQL, ex, sym, market, side,
                                      str(self._cfg.depth_lookback_hours))
        books: dict = defaultdict(list)
        for r in rows:
            books[r["ts"]].append((r["price"], r["size_usd"]))
        by_hod: dict[int, list[Curve]] = defaultdict(list)
        for ts, levels in books.items():
            if len(levels) < 3:
                continue
            c = Curve(levels, side)
            if c.f:
                by_hod[ts.astimezone(dt.timezone.utc).hour].append(c)
        self._cache[key] = (_t.monotonic(), by_hod)
        return by_hod

    async def latest_curve(self, ex: str, sym: str, market: str, side: str
                           ) -> Curve | None:
        """The most recent book — what a paper entry actually executes against."""
        rows = await self._pool.fetch(
            """SELECT price, size_usd FROM carry_book_l2
               WHERE exchange=$1 AND symbol=$2 AND market=$3 AND side=$4
                 AND ts = (SELECT max(ts) FROM carry_book_l2
                           WHERE exchange=$1 AND symbol=$2 AND market=$3)
                 AND price > 0 AND size_usd IS NOT NULL AND size_usd > 0""",
            ex, sym, market, side)
        levels = [(r["price"], r["size_usd"]) for r in rows]
        if len(levels) < 3:
            return None
        c = Curve(levels, side)
        return c if c.f else None


def worst_hour_capacity(by_hod: dict[int, list[Curve]], t_bps: float,
                        cfg) -> tuple[float, int | None, str]:
    """Thinnest hour-of-day capacity.

    Returns (usd, hour, basis). `basis` records HOW the number was reached, so
    a degraded reading can never be mistaken for a real diurnal worst case:
      'worst-hour'   — enough hour-of-day buckets for a true diurnal minimum
      'p10-limited'  — too few buckets yet; 10th-percentile snapshot instead
      'no-data'      — nothing usable
    """
    buckets = [(statistics.median([c.capacity(t_bps) for c in curves]), h)
               for h, curves in by_hod.items()
               if len(curves) >= cfg.min_snaps_per_hod]
    if len(buckets) >= cfg.min_hod_buckets:
        cap, hour = min(buckets)
        return cap, hour, "worst-hour"
    allc = [c for curves in by_hod.values() for c in curves]
    if not allc:
        return 0.0, None, "no-data"
    caps = sorted(c.capacity(t_bps) for c in allc)
    return caps[max(0, int(0.10 * (len(caps) - 1)))], None, "p10-limited"


def worst_hour_slip(by_hod: dict[int, list[Curve]], usd: float, cfg) -> float:
    """Worst hour-of-day slippage at `usd`: max over buckets of bucket median.
    inf when a bucket's book cannot absorb the size at all."""
    worst = 0.0
    seen = 0
    for curves in by_hod.values():
        if len(curves) < cfg.min_snaps_per_hod:
            continue
        vals = [c.slip_bps(usd) for c in curves]
        ok = [v for v in vals if v is not None]
        if not vals or len(ok) / len(vals) < 0.5:
            return float("inf")
        seen += 1
        worst = max(worst, statistics.median(ok))
    if not seen:
        return float("inf")
    return worst


async def round_trip_slip(books: "BookSource", ex: str, sym: str, usd: float,
                          cfg) -> tuple[float, dict[str, float]]:
    """Worst-hour four-leg round-trip slippage in bps, plus the per-leg split."""
    total = 0.0
    per_leg: dict[str, float] = {}
    for market, side, label in LEGS:
        by_hod = await books.leg_curves(ex, sym, market, side)
        s = worst_hour_slip(by_hod, usd, cfg)
        per_leg[label] = s
        total += s
    return total, per_leg


async def max_prudent_notional(books: "BookSource", ex: str, sym: str, cfg
                               ) -> tuple[float, float]:
    """Largest per-leg USD notional keeping worst-hour round-trip slippage
    under the cap. Returns (usd, slip_bps). This is R6 — the size cap is a
    WORST-HOUR number, never a median one."""
    cap = cfg.max_rt_slip_bps
    lo, hi = 0.0, 200_000.0
    first, _ = await round_trip_slip(books, ex, sym, cfg.min_notional_usd, cfg)
    if first > cap:
        return 0.0, first
    top, _ = await round_trip_slip(books, ex, sym, hi, cfg)
    if top <= cap:
        return hi, top
    for _ in range(24):
        mid = (lo + hi) / 2
        s, _ = await round_trip_slip(books, ex, sym, mid, cfg)
        if s <= cap:
            lo = mid
        else:
            hi = mid
    s, _ = await round_trip_slip(books, ex, sym, lo, cfg)
    return lo, s
