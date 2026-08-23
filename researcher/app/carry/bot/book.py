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
from collections import OrderedDict, defaultdict

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


# SAMPLED, not exhaustive (2026-08-23). The old query pulled EVERY level of
# EVERY snapshot in the 168h window — up to 304,200 rows for one leg of one
# name. Held as Curve objects across a 153-name pass in an unbounded cache,
# that is what took RSS from 1 GB to 6 GB in a single selection.
#
# The statistic we actually need is a MEDIAN per hour-of-day bucket. A median
# over ~250 snapshots and a median over ~28 are the same number to well inside
# the noise, so we sample instead of hoarding.
#
# Sampling is per (DAY, hour), not per hour-of-day: taking the N most recent
# snapshots per hour-of-day would draw them all from the last 24h and destroy
# the diurnal structure the worst-hour statistic exists to measure.
_LEG_SQL = """
WITH snaps AS (
    SELECT DISTINCT ts
    FROM carry_book_l2
    WHERE exchange=$1 AND symbol=$2 AND market=$3 AND side=$4
          AND ts > now() - ($5 || ' hours')::interval
), ranked AS (
    SELECT ts, row_number() OVER (
               PARTITION BY (ts AT TIME ZONE 'UTC')::date,
                            extract(hour FROM ts AT TIME ZONE 'UTC')
               ORDER BY ts DESC) AS rn
    FROM snaps
)
SELECT b.ts, b.price, b.size_usd
FROM ranked r
JOIN carry_book_l2 b
  ON b.ts = r.ts AND b.exchange=$1 AND b.symbol=$2
     AND b.market=$3 AND b.side=$4
WHERE r.rn <= $6
      AND b.price > 0 AND b.size_usd IS NOT NULL AND b.size_usd > 0
ORDER BY b.ts, b.level
"""


class BookSource:
    """Loads and caches per-leg curves grouped by hour-of-day.

    The cache is an LRU with a HARD CAP. It exists so the 24-step binary search
    in max_prudent_notional does not re-query the same four legs ~100 times per
    name — a within-name optimisation. Keeping every name's curves for the whole
    pass bought nothing and cost gigabytes.
    """

    def __init__(self, pool, cfg) -> None:
        self._pool = pool
        self._cfg = cfg
        self._cache: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
        self._cache_secs = 300.0
        self._max_entries = int(getattr(cfg, "book_cache_entries", 16))

    def reset_cache(self) -> None:
        """Drop everything. Called between selection passes so peak RSS is set
        by the cap, not by how many names the universe happens to hold."""
        self._cache.clear()

    def _remember(self, key, value) -> None:
        import time as _t
        self._cache[key] = (_t.monotonic(), value)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)         # evict least-recently-used

    async def leg_curves(self, ex: str, sym: str, market: str, side: str
                         ) -> dict[int, list[Curve]]:
        """-> {hour_of_day: [Curve]} over the depth lookback window."""
        import time as _t
        key = (ex, sym, market, side)
        hit = self._cache.get(key)
        if hit and _t.monotonic() - hit[0] < self._cache_secs:
            self._cache.move_to_end(key)
            return hit[1]
        rows = await self._pool.fetch(
            _LEG_SQL, ex, sym, market, side,
            str(self._cfg.depth_lookback_hours),
            int(self._cfg.max_snaps_per_hour))
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
        books.clear()                                # release before the next leg
        del rows
        self._remember(key, by_hod)
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


class PairedMarks:
    """Both legs of one name marked from the SAME instant.

    ``skew_sec`` is how far apart the two books actually are, ``age_min`` how
    old the pair is. Neutrality is a DIFFERENCE between two legs, so a
    consistent pair a few minutes old is far better evidence than two fresh
    marks taken minutes apart.
    """

    __slots__ = ("spot", "perp", "ts", "skew_sec", "age_min")

    def __init__(self, spot, perp, ts, skew_sec, age_min):
        self.spot, self.perp, self.ts = spot, perp, ts
        self.skew_sec, self.age_min = skew_sec, age_min


# Newest SPOT book, and the PERP book nearest to it in time. Spot is the sparse
# leg (mexc spot depth averages a 7.9 min gap against perp's 2.0 min), so it
# sets the clock and perp is matched to it — never the reverse.
_PAIR_SQL = """
WITH s_ts AS (
    SELECT max(ts) AS ts FROM carry_book_l2
     WHERE exchange=$1 AND symbol=$2 AND market='spot'
       AND ts > now() - interval '6 hours'
), p_ts AS (
    SELECT ts FROM carry_book_l2
     WHERE exchange=$1 AND symbol=$2 AND market='perp'
       AND ts > now() - interval '6 hours'
       AND (SELECT ts FROM s_ts) IS NOT NULL
     ORDER BY abs(extract(epoch FROM ts - (SELECT ts FROM s_ts))) ASC
     LIMIT 1
)
SELECT market, ts,
       max(price) FILTER (WHERE side='bid') AS bid,
       min(price) FILTER (WHERE side='ask') AS ask,
       extract(epoch FROM (now() - ts))/60.0 AS age_min
  FROM carry_book_l2
 WHERE exchange=$1 AND symbol=$2 AND size_usd > 0 AND price > 0
   AND ((market='spot' AND ts = (SELECT ts FROM s_ts))
     OR (market='perp' AND ts = (SELECT ts FROM p_ts)))
 GROUP BY market, ts
"""


async def paired_mids(pool, ex: str, sym: str,
                      max_skew_sec: float) -> "PairedMarks | None":
    """Spot and perp MIDs from books taken at the same instant, or None.

    THIS IS THE FIX FOR THE SECOND HALF OF THE PHANTOM-DRIFT BUG. Pinning the
    source per venue (3b/3-A) stopped the mark from flipping between feeds, but
    each leg was still taken from its own newest book — and the two collectors
    do not tick together: perp lands every ~2 min, mexc spot every ~8 min and
    sometimes 32. Marking a spot leg 8 minutes stale against a fresh perp turns
    the coin's OWN movement into delta. mexc/BTW fell 5% in 19 min, which read
    as +4.1% delta, a rebalance, and then -3.2% the moment a fresh spot book
    landed — a sawtooth with the source already pinned.

    A mid, not a bid: comparing one leg's mid against the other's touch bakes in
    a systematic half-spread step.

    Returns None when either leg is missing or the two books are further apart
    than `max_skew_sec`. The caller must then leave neutrality UNEVALUATED —
    never substitute an unpaired mark, which is the bug this removes.
    """
    rows = await pool.fetch(_PAIR_SQL, ex, sym)
    legs = {r["market"]: r for r in rows}
    s, p = legs.get("spot"), legs.get("perp")
    if not s or not p:
        return None
    if not (s["bid"] and s["ask"] and p["bid"] and p["ask"]):
        return None
    skew = abs((s["ts"] - p["ts"]).total_seconds())
    if skew > max_skew_sec:
        return None
    return PairedMarks(
        spot=(float(s["bid"]) + float(s["ask"])) / 2.0,
        perp=(float(p["bid"]) + float(p["ask"])) / 2.0,
        ts=s["ts"],
        skew_sec=skew,
        age_min=max(float(s["age_min"]), float(p["age_min"])),
    )


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
