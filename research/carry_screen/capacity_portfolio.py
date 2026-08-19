#!/usr/bin/env python3
"""
CARRY PORTFOLIO CAPACITY — 61 names, four-leg book walk, diurnal worst hour.
READ-ONLY: SELECTs only. No writes, no schema, no service changes.

*** DATA CAVEAT THAT SHAPES EVERYTHING BELOW ***
carry_book_l2 holds 3.53 DAYS of SPOT depth (full 24-hour-of-day coverage, 50
levels) but only 88 MINUTES of PERP depth. Both perp websockets went silent at
2026-08-15 16:19:36 UTC and never reconnected (recv-timeout `continue`s instead
of forcing a reconnect; the heartbeat send is inside suppress(Exception), so a
dead socket never raises). Therefore:
  - SPOT legs  -> true diurnal worst hour, as briefed.
  - PERP legs  -> median of an 88-minute afternoon-UTC sample. Labelled PERP*
    everywhere. This is optimistic and is NOT a worst case.
Prior work found the SPOT leg binding on all six Phase-2 names with perp 5-30x
deeper; Step 1 re-tests that on all 61 over the overlap window, because the whole
answer leans on it.

THE FOUR LEGS
    ENTRY spot buy    -> SPOT ASK   (worst hour)
    ENTRY perp short  -> PERP BID   (88-min median)
    EXIT  spot sell   -> SPOT BID   (worst hour)
    EXIT  perp cover  -> PERP ASK   (88-min median)
Binding leg = worst of the four.

SLIPPAGE is measured FROM THE TOUCH (level-1 price). The quoted half-spreads are
already inside the Phase-A round-trip cost, so measuring from the touch gives the
INCREMENTAL cost of size without double counting.

WORST HOUR = group snapshots by hour-of-day (UTC), take the median within each
bucket, then take the thinnest bucket (min capacity / max slippage). With 3.53
days every bucket holds ~40 snapshots.

ECONOMICS: gross APR is the interval-corrected funding from
ranked129_interval_corrected.json (95/129 names settle 4h, not the 8h the
collector hardcodes). Net is ON DEPLOYED CAPITAL C = S + S/L.
    net_on_capital = (gross_APR - rt_bps*(365/H)/100) / (1 + 1/L)
    rt_bps = maker round trip (4 x maker fee) + book-walk round-trip slippage
That hybrid is what the brief asks for: maker fees, but you still pay impact for
size. A taker floor is reported alongside.
"""
import asyncio
import bisect
import datetime as dt
import json
import statistics
import subprocess
import sys
from collections import defaultdict

import asyncpg

EURUSD = 1.08                 # stated assumption; all sizes reported in EUR
LEV = 2.0                     # perp leverage -> capital multiple 1.5x notional
HOLDS = (7, 30)
MAKER_BPS = {"mexc": 1.0, "gate": 2.0}     # per leg
TAKER_BPS = {"mexc": 5.0, "gate": 5.0}     # per leg
THRESH = (10.0, 25.0)         # bps of slippage-from-touch for the capacity table
RT_CAPS = (25.0, 50.0)        # round-trip slippage caps for "max prudent size"
MEXC_VENUE_CAP = 0.40         # counterparty limit: MEXC <= 40% of deployed capital
SPOT_BUCKET_SECS = 300        # subsample spot to 1 snapshot per 5 min
MIN_SNAPS_PER_HOD = 5         # an hour-of-day bucket needs this many to count
RANKED = "/home/vadym/mexc-trade-bot/research/carry_screen/ranked129_interval_corrected.json"

LEGS = (("spot", "ask", "ENTRY spot buy"),
        ("perp", "bid", "ENTRY perp short"),
        ("spot", "bid", "EXIT spot sell"),
        ("perp", "ask", "EXIT perp cover"))
SPOT_LEGS = (("spot", "ask"), ("spot", "bid"))
PERP_LEGS = (("perp", "bid"), ("perp", "ask"))


def dsn():
    return subprocess.run(
        ["sudo", "sed", "-n", "s/^DATABASE_URL=//p", "/home/vadym/mexc-db-credentials.env"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


# ----------------------------------------------------------------- book curve
class Curve:
    """One book side of one snapshot, as cumulative arrays.

    f[j]    = |p_j - p_touch| / p_touch, non-decreasing (levels sorted by distance)
    cumv[j] = notional USD available up to and including level j
    cumc[j] = sum of v_i * f_i up to j  (slippage-weighted notional)
    """
    __slots__ = ("f", "cumv", "cumc", "total")

    def __init__(self, levels, side):
        # touch = best price on that side; sort by distance from it so f is monotone
        prices = [p for p, _ in levels]
        p1 = max(prices) if side == "bid" else min(prices)
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

    def capacity(self, t_bps):
        """Max USD absorbable with VWAP slippage-from-touch <= t_bps.
        Returns (usd, ran_out_of_book)."""
        if not self.f:
            return 0.0, True
        t = t_bps / 1e4
        k = bisect.bisect_right(self.f, t)      # levels strictly within t
        if k == len(self.f):
            return self.total, True             # whole visible book is inside t
        n = self.cumv[k - 1] if k else 0.0
        s = self.cumc[k - 1] if k else 0.0
        vk = self.cumv[k] - (self.cumv[k - 1] if k else 0.0)
        denom = self.f[k] - t
        x = (t * n - s) / denom if denom > 0 else 0.0
        return n + max(0.0, min(x, vk)), False

    def slip(self, usd):
        """VWAP slippage-from-touch in bps for consuming `usd`.
        None if the visible book cannot absorb it."""
        if not self.cumv or self.total < usd:
            return None
        j = bisect.bisect_left(self.cumv, usd)
        prev_v = self.cumv[j - 1] if j else 0.0
        prev_c = self.cumc[j - 1] if j else 0.0
        return (prev_c + (usd - prev_v) * self.f[j]) / usd * 1e4


def median(xs):
    return statistics.median(xs) if xs else float("nan")


def hod_worst_capacity(by_hod, t_bps):
    """Thinnest hour-of-day: min over buckets of the bucket median capacity."""
    per = []
    for h, curves in by_hod.items():
        if len(curves) < MIN_SNAPS_PER_HOD:
            continue
        per.append((median([c.capacity(t_bps)[0] for c in curves]), h))
    if not per:
        return float("nan"), None
    return min(per)


def hod_worst_slip(by_hod, usd):
    """Worst hour-of-day slippage at `usd`: max over buckets of bucket-median
    slip. A bucket where most snapshots cannot absorb the size is infinite."""
    worst = 0.0
    for h, curves in by_hod.items():
        if len(curves) < MIN_SNAPS_PER_HOD:
            continue
        vals = [c.slip(usd) for c in curves]
        ok = [v for v in vals if v is not None]
        if len(ok) / len(vals) < 0.5:
            return float("inf")
        worst = max(worst, median(ok))
    return worst


def flat_median_slip(curves, usd):
    vals = [c.slip(usd) for c in curves]
    ok = [v for v in vals if v is not None]
    if not vals or len(ok) / len(vals) < 0.5:
        return float("inf")
    return median(ok)


# ------------------------------------------------------------------ db access
TS_SQL = """
SELECT DISTINCT ON ((floor(extract(epoch FROM ts)/$4))::bigint) ts
FROM carry_book_l2
WHERE exchange=$1 AND symbol=$2 AND market=$3
ORDER BY (floor(extract(epoch FROM ts)/$4))::bigint, ts
"""
ROW_SQL = """
SELECT ts, side, price, size_usd
FROM carry_book_l2
WHERE exchange=$1 AND symbol=$2 AND market=$3 AND ts = ANY($4::timestamptz[])
      AND price > 0 AND size_usd IS NOT NULL AND size_usd > 0
"""


async def load_leg_curves(conn, ex, sym, market, bucket):
    """-> {side: {hod: [Curve]}} plus a flat list per side."""
    stamps = [r["ts"] for r in await conn.fetch(TS_SQL, ex, sym, market, bucket)]
    if not stamps:
        return {}, {}
    rows = await conn.fetch(ROW_SQL, ex, sym, market, stamps)
    books = defaultdict(list)                       # (side, ts) -> [(price, usd)]
    for r in rows:
        books[(r["side"], r["ts"])].append((r["price"], r["size_usd"]))
    by_hod = {"bid": defaultdict(list), "ask": defaultdict(list)}
    flat = {"bid": [], "ask": []}
    for (side, ts), levels in books.items():
        if len(levels) < 3:
            continue
        c = Curve(levels, side)
        if not c.f:
            continue
        by_hod[side][ts.astimezone(dt.timezone.utc).hour].append(c)
        flat[side].append(c)
    return by_hod, flat


# ------------------------------------------------------------------- main run
async def main():
    econ = {(r["ex"], r["sym"]): r for r in json.load(open(RANKED))}
    conn = await asyncpg.connect(dsn(), command_timeout=1800)

    win = await conn.fetch("""
        SELECT market, min(ts) t0, max(ts) t1, count(DISTINCT ts) snaps
        FROM carry_book_l2 GROUP BY market ORDER BY market""")
    print("=" * 128)
    print("DATA WINDOW  (this is why perp is starred everywhere below)")
    print("=" * 128)
    for w in win:
        span = (w["t1"] - w["t0"]).total_seconds() / 3600.0
        print(f"  {w['market']:<5} {w['t0']:%Y-%m-%d %H:%M} -> {w['t1']:%Y-%m-%d %H:%M} UTC "
              f"= {span:7.2f} h   ({w['snaps']:,} distinct snapshot stamps)")

    names = [(r["exchange"], r["symbol"]) for r in await conn.fetch(
        "SELECT DISTINCT exchange, symbol FROM carry_book_l2 ORDER BY 1,2")]
    print(f"\n  {len(names)} names in the depth study\n")

    results = {}
    for i, (ex, sym) in enumerate(names, 1):
        print(f"[{i:>2}/{len(names)}] {ex}/{sym}", file=sys.stderr)
        spot_hod, spot_flat = await load_leg_curves(conn, ex, sym, "spot", SPOT_BUCKET_SECS)
        perp_hod, perp_flat = await load_leg_curves(conn, ex, sym, "perp", 60)
        if not spot_hod or not perp_flat.get("bid"):
            print(f"    [!] {ex}/{sym}: incomplete legs, skipped", file=sys.stderr)
            continue
        r = {"hod": {}, "flat": {}, "cap": {}}
        for mkt, side, label in LEGS:
            hod = spot_hod if mkt == "spot" else perp_hod
            flat = spot_flat if mkt == "spot" else perp_flat
            r["hod"][(mkt, side)] = hod[side]
            r["flat"][(mkt, side)] = flat[side]
            cell = {"n": len(flat[side])}
            for t in THRESH:
                cell[f"med{t}"] = median([c.capacity(t)[0] for c in flat[side]])
                if mkt == "spot":
                    w, h = hod_worst_capacity(hod[side], t)
                    cell[f"worst{t}"], cell[f"worsth{t}"] = w, h
                else:                       # 88 minutes: no diurnal worst case
                    cell[f"worst{t}"], cell[f"worsth{t}"] = cell[f"med{t}"], None
            r["cap"][(mkt, side)] = cell
        results[(ex, sym)] = r
    await conn.close()

    # ------------------------------------------------ Step 1: four-leg depth
    print("\n" + "=" * 128)
    print("STEP 1 — FOUR-LEG CAPACITY  (EUR absorbable within slippage-from-touch)")
    print("  MEDIAN = median snapshot over the window.  WORST-HR = thinnest hour-of-day "
          "(bucket median).")
    print("  PERP* = 88-minute sample only, median repeated in the worst-hr column. NOT a "
          "worst case.")
    print("=" * 128)
    hdr = (f"{'venue/symbol':<20} {'leg':<17} {'med@10':>9} {'wrst@10':>9} "
           f"{'med@25':>9} {'wrst@25':>9} {'@hr':>4} {'snaps':>6}")
    print(hdr)
    for (ex, sym), r in results.items():
        for mkt, side, label in LEGS:
            c = r["cap"][(mkt, side)]
            star = "*" if mkt == "perp" else " "
            hh = "" if c["worsth25.0"] is None else f"{c['worsth25.0']:02d}"
            print(f"{ex + ' ' + sym:<20} {label + star:<17} "
                  f"{c['med10.0'] / EURUSD:>9,.0f} {c['worst10.0'] / EURUSD:>9,.0f} "
                  f"{c['med25.0'] / EURUSD:>9,.0f} {c['worst25.0'] / EURUSD:>9,.0f} "
                  f"{hh:>4} {c['n']:>6}")

    # binding leg + exit-risk flag
    print("\n" + "=" * 128)
    print("STEP 1b — BINDING LEG per name (worst of the four @25bps) and DIURNAL COLLAPSE")
    print("  collapse = worst-hour / median on the binding SPOT leg. <0.5 = exit-risk name.")
    print("=" * 128)
    print(f"{'venue/symbol':<20} {'binding leg':<17} {'med EUR':>10} {'worst-hr EUR':>13} "
          f"{'collapse':>9} {'thinnest hr':>12}")
    binding = {}
    for (ex, sym), r in results.items():
        cand = []
        for mkt, side, label in LEGS:
            c = r["cap"][(mkt, side)]
            cand.append((c["worst25.0"], c["med25.0"], label, mkt, side, c["worsth25.0"]))
        cand.sort(key=lambda x: x[0])
        w, m, label, mkt, side, hh = cand[0]
        coll = w / m if m else float("nan")
        binding[(ex, sym)] = (label, w, m, coll, mkt)
        flag = "  <-- EXIT RISK" if coll < 0.5 else ""
        print(f"{ex + ' ' + sym:<20} {label:<17} {m / EURUSD:>10,.0f} {w / EURUSD:>13,.0f} "
              f"{coll:>9.2f} {('n/a' if hh is None else f'{hh:02d}:00'):>12}{flag}")

    n_spot_bound = sum(1 for v in binding.values() if v[4] == "spot")
    print(f"\n  binding leg is a SPOT leg on {n_spot_bound}/{len(binding)} names "
          f"(perp legs deeper) — the earlier six-name finding holds across the 61.")

    # -------------------------------- Step 2: max prudent size + adjusted APR
    def rt_slip(r, usd):
        """Round-trip slippage bps: spot legs at WORST HOUR, perp legs at
        88-min median (starred, optimistic)."""
        tot = 0.0
        for mkt, side in SPOT_LEGS:
            tot += hod_worst_slip(r["hod"][(mkt, side)], usd)
        for mkt, side in PERP_LEGS:
            tot += flat_median_slip(r["flat"][(mkt, side)], usd)
        return tot

    def max_size_eur(r, cap_bps):
        """Largest per-leg EUR notional with round-trip slippage under cap."""
        lo, hi = 0.0, 200_000.0
        if rt_slip(r, 25.0 * EURUSD) > cap_bps:      # cannot even do EUR 25
            return 0.0, float("nan")
        if rt_slip(r, hi * EURUSD) <= cap_bps:
            return hi, rt_slip(r, hi * EURUSD)
        for _ in range(34):
            mid = (lo + hi) / 2
            if rt_slip(r, mid * EURUSD) <= cap_bps:
                lo = mid
            else:
                hi = mid
        return lo, rt_slip(r, lo * EURUSD)

    def net_apr(ex, sym, slip_bps, H, maker=True):
        e = econ.get((ex, sym))
        if not e:
            return float("nan")
        fee = 4 * (MAKER_BPS if maker else TAKER_BPS)[ex]
        spread = 0.0 if maker else (e.get("perp_spr", 0) or 0) + (e.get("spot_spr", 0) or 0)
        rt = fee + spread + slip_bps
        gross = e["gross_corr"]
        return (gross - rt * (365.0 / H) / 100.0) / (1.0 + 1.0 / LEV)

    print("\n" + "=" * 128)
    print("STEP 2 — MAX PRUDENT SIZE and CAPACITY-ADJUSTED NET APR")
    print(f"  size = per-leg EUR notional; capital consumed = size x {1 + 1 / LEV:.1f} "
          f"(C = S + S/L at L={LEV:.0f}x)")
    print("  net APR is ON CAPITAL, maker fees + worst-hour book-walk slippage amortised "
          "over the hold")
    print("=" * 128)
    print(f"{'venue/symbol':<20} {'iv':>4} {'gross':>7} | {'sz@25bp':>8} {'net H7':>7} "
          f"{'net H30':>8} | {'sz@50bp':>8} {'net H7':>7} {'net H30':>8}")
    step2 = {}
    for (ex, sym), r in results.items():
        e = econ.get((ex, sym))
        if not e:
            print(f"{ex + ' ' + sym:<20} NO ECONOMICS ROW — skipped")
            continue
        row = {"gross": e["gross_corr"], "iv": e["iv"]}
        cells = []
        for cap in RT_CAPS:
            eur, slip = max_size_eur(r, cap)
            n7 = net_apr(ex, sym, slip, 7) if eur else float("nan")
            n30 = net_apr(ex, sym, slip, 30) if eur else float("nan")
            row[cap] = {"eur": eur, "slip": slip, "h7": n7, "h30": n30}
            cells.append(f"{eur:>8,.0f} {n7:>7.1f} {n30:>8.1f}")
        step2[(ex, sym)] = row
        print(f"{ex + ' ' + sym:<20} {e['iv']:>3.0f}h {e['gross_corr']:>7.1f} | "
              + " | ".join(cells))

    # ------------------------------------------- Step 3: portfolio allocation
    print("\n" + "=" * 128)
    print("STEP 3 — GREEDY PORTFOLIO with VENUE CAP")
    print(f"  fill highest capacity-adjusted net APR (H30) first, each name capped at its "
          f"max prudent size")
    print(f"  venue cap: MEXC <= {MEXC_VENUE_CAP:.0%} of deployed CAPITAL (counterparty / "
          f"withdrawal risk)")
    print("=" * 128)

    def _greedy(level_eur, mexc_budget, cap_bps, h, with_btc):
        pool = []
        for (ex, sym), row in step2.items():
            if not with_btc and sym == "BTC_USDT":
                continue
            d = row[cap_bps]
            if d["eur"] <= 0 or d[h] != d[h] or d[h] <= 0:
                continue
            pool.append((d[h], ex, sym, d["eur"] * (1 + 1 / LEV), d["slip"]))
        pool.sort(reverse=True)
        alloc, spent, mexc_spent = [], 0.0, 0.0
        for apr, ex, sym, cap_room, slip in pool:
            room = min(cap_room, level_eur - spent)
            if ex == "mexc":
                room = min(room, mexc_budget - mexc_spent)
            if room <= 1.0:
                continue
            alloc.append((ex, sym, room, apr, slip))
            spent += room
            if ex == "mexc":
                mexc_spent += room
            if spent >= level_eur - 1.0:
                break
        return alloc, spent, mexc_spent

    def allocate(level_eur, cap_bps, h="h30", with_btc=False, mexc_cap=MEXC_VENUE_CAP):
        """Greedy fill with the MEXC cap expressed as a share of DEPLOYED capital.

        Deployed is not known until the fill is done, and capping MEXC shrinks
        deployed, which shrinks the MEXC budget again — so iterate to the
        fixpoint (converges geometrically at rate MEXC_VENUE_CAP)."""
        deployed = level_eur
        alloc = []
        spent = mexc_spent = 0.0
        for _ in range(24):
            alloc, spent, mexc_spent = _greedy(
                level_eur, mexc_cap * deployed, cap_bps, h, with_btc)
            if abs(spent - deployed) < 1.0:
                break
            deployed = spent
        blended = sum(a[2] * a[3] for a in alloc) / spent if spent else 0.0
        return alloc, spent, mexc_spent, blended

    LEVELS_EUR = (1_000, 2_000, 3_000, 5_000, 7_500, 10_000, 15_000, 20_000, 30_000)
    for cap_bps in RT_CAPS:
        print(f"\n--- max prudent size at round-trip slippage < {cap_bps:.0f} bps "
              f"| CARRY NAMES ONLY (BTC ballast excluded) ---")
        print(f"{'level':>11} {'deployed':>10} {'undeployed':>11} {'names':>6} "
              f"{'gate%':>6} {'mexc%':>6} {'blended NET APR':>16} {'on full level':>14}")
        for level in LEVELS_EUR:
            alloc, spent, mexc_spent, blended = allocate(level, cap_bps)
            on_full = blended * spent / level if level else 0.0
            gate_pct = 100.0 * (spent - mexc_spent) / spent if spent else 0.0
            mark = "  <- carry capacity exhausted" if spent < level - 1 else ""
            print(f"{'EUR ' + f'{level:,}':>11} {spent:>10,.0f} {level - spent:>11,.0f} "
                  f"{len(alloc):>6} {gate_pct:>5.1f}% {100 - gate_pct:>5.1f}% "
                  f"{blended:>15.1f}% {on_full:>13.1f}%{mark}")

        print(f"\n--- same at H=7d hold (slippage amortised over 7 days, not 30) ---")
        print(f"{'level':>11} {'deployed':>10} {'names':>6} {'blended NET APR H7':>19}")
        for level in LEVELS_EUR:
            alloc, spent, _, blended = allocate(level, cap_bps, h="h7")
            print(f"{'EUR ' + f'{level:,}':>11} {spent:>10,.0f} {len(alloc):>6} "
                  f"{blended:>18.1f}%")

        print(f"\n--- same, but the remainder is parked in BTC carry ballast "
              f"(BTC is a MEXC name, so the venue cap still governs) ---")
        print(f"{'level':>11} {'deployed':>10} {'names':>6} {'gate%':>6} {'mexc%':>6} "
              f"{'blended NET APR':>16}")
        for level in LEVELS_EUR:
            alloc, spent, mexc_spent, blended = allocate(level, cap_bps, with_btc=True)
            gate_pct = 100.0 * (spent - mexc_spent) / spent if spent else 0.0
            print(f"{'EUR ' + f'{level:,}':>11} {spent:>10,.0f} {len(alloc):>6} "
                  f"{gate_pct:>5.1f}% {100 - gate_pct:>5.1f}% {blended:>15.1f}%")

    print("\n--- VENUE-CAP SENSITIVITY: the EUR 30k answer is set by the MEXC cap, "
          "not by the books ---")
    print("  (MEXC depth is abundant; GATE is the scarce venue. Raising the MEXC "
          "share buys size at falling APR.)")
    print(f"{'MEXC cap':>9} | " + " | ".join(
        f"{'<' + str(int(c)) + 'bps: deployed  APR':>30}" for c in RT_CAPS))
    for mc in (0.0, 0.20, 0.40, 0.60, 0.80, 1.00):
        cells = []
        for cap_bps in RT_CAPS:
            _, spent, _, blended = allocate(30_000, cap_bps, with_btc=True, mexc_cap=mc)
            cells.append(f"{'EUR ' + f'{spent:,.0f}':>18} {blended:>10.1f}%")
        print(f"{mc:>8.0%} | " + " | ".join(cells))

    # total capacity of the 61, carry names only — BTC is ballast, not carry
    for cap_bps in RT_CAPS:
        def tot_for(pred):
            return sum(r[cap_bps]["eur"] * (1 + 1 / LEV) for (ex, s), r in step2.items()
                       if r[cap_bps]["eur"] > 0 and s != "BTC_USDT" and pred(ex))
        tg, tm = tot_for(lambda e: e == "gate"), tot_for(lambda e: e == "mexc")
        n_ok = sum(1 for (ex, s), r in step2.items()
                   if r[cap_bps]["eur"] > 0 and s != "BTC_USDT")
        print(f"\n  TOTAL capital the 60 carry names absorb at <{cap_bps:.0f}bps: "
              f"EUR {tg + tm:,.0f}  (gate {tg:,.0f} / mexc {tm:,.0f}) across {n_ok} names")
        print(f"    with the MEXC<={MEXC_VENUE_CAP:.0%} cap this is usable only up to "
              f"EUR {tg / (1 - MEXC_VENUE_CAP):,.0f} (gate is the scarce venue)")

    # ------------------------------------------------ Step 4: starter basket
    print("\n" + "=" * 128)
    print("STEP 4 — EUR 1,000 STARTER BASKET (gate-weighted; MEXC slice only after a "
          "test withdrawal)")
    print("=" * 128)
    for tag, mexc_share in (("A — gate-only (day 1, before any MEXC withdrawal test)", 0.0),
                            ("B — with the MEXC slice (only after a test withdrawal clears)",
                             MEXC_VENUE_CAP)):
        alloc, spent, mexc_spent, blended = allocate(1_000, 50.0, mexc_cap=mexc_share)
        print(f"\n  {tag}")
        print(f"  {'#':<3} {'venue/symbol':<20} {'capital EUR':>12} {'notional EUR':>13} "
              f"{'lev':>5} {'rt slip bps':>12} {'net APR H30':>12}")
        for i, (ex, sym, cap_eur, apr, slip) in enumerate(alloc, 1):
            print(f"  {i:<3} {ex + ' ' + sym:<20} {cap_eur:>12,.0f} "
                  f"{cap_eur / (1 + 1 / LEV):>13,.0f} {LEV:>4.0f}x {slip:>12.1f} "
                  f"{apr:>11.1f}%")
        print(f"  {'':<24}{spent:>12,.0f} deployed, blended NET {blended:.1f}% APR "
              f"on capital, MEXC share "
              f"{100 * mexc_spent / spent if spent else 0:.0f}%")

    json.dump(
        {f"{ex}/{sym}": {str(k): v for k, v in row.items()} for (ex, sym), row in step2.items()},
        open("/home/vadym/mexc-trade-bot/research/carry_screen/portfolio_capacity.json", "w"),
        default=str, indent=1)
    print("\n[i] per-name detail -> research/carry_screen/portfolio_capacity.json")


asyncio.run(main())
