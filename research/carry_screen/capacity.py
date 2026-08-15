#!/usr/bin/env python3
"""
CARRY CAPACITY — four-leg book walk (read-only).

*** PRELIMINARY: only ~12.6 minutes of carry_book_l2 exists at run time. ***
Median depth is measurable. WORST-HOUR IS NOT — there is no hour, let alone a
day, to take a 5th-percentile hour from. Where the brief asks for worst-hour
this reports the 5th-percentile SNAPSHOT inside the short window and labels it
as such. It is a dispersion measure, not a diurnal worst case.

The four legs, each limited by a different book side:
    ENTRY spot buy    -> SPOT ASK
    ENTRY perp short  -> PERP BID
    EXIT  spot sell   -> SPOT BID
    EXIT  perp cover  -> PERP ASK
Binding leg = worst of the four.

Slippage is measured FROM THE TOUCH (level-1 price), deliberately: the quoted
half-spreads are already charged in the Phase A round-trip cost, so measuring
from the touch gives the INCREMENTAL cost of size without double counting.

VWAP walk: consuming n_j of level j at per-unit slippage f_j = (p_j-p1)/p1,
the notional-weighted slippage after N is sum(n_j*f_j)/N. Max N within
threshold t is solved exactly, allowing a partial fill of the level that
breaches t.
"""
import asyncio, json, statistics, subprocess, sys
from collections import defaultdict
import asyncpg

BASKET = [("gate", "HANA_USDT"), ("gate", "WET_USDT"), ("gate", "IDOL_USDT"),
          ("gate", "BTR_USDT"), ("mexc", "PLAY_USDT"), ("mexc", "BTC_USDT")]

# Phase A corrected economics (interval-corrected gross APR on notional,
# and the infinitesimal-size taker round-trip cost in bps)
PHASE_A = {
    ("mexc", "PLAY_USDT"): {"gross": 58.99, "rt": 41.8},
    ("gate", "WET_USDT"):  {"gross": 59.64, "rt": 43.6},
    ("gate", "HANA_USDT"): {"gross": 57.00, "rt": 37.6},
    ("gate", "IDOL_USDT"): {"gross": 53.12, "rt": 33.7},
    ("gate", "BTR_USDT"):  {"gross": 32.09, "rt": 41.7},
    ("mexc", "BTC_USDT"):  {"gross":  6.36, "rt": 20.0},
}
EURUSD = 1.08                      # stated assumption; sizes given in EUR
SIZES_EUR = [500, 2000, 10000]
THRESH_BPS = [10.0, 25.0]
LEV = 2.0                          # perp leverage for on-capital conversion
HOLDS = [7, 30]

LEGS = [("spot", "ask", "ENTRY spot buy"), ("perp", "bid", "ENTRY perp short"),
        ("spot", "bid", "EXIT spot sell"), ("perp", "ask", "EXIT perp cover")]


def dsn():
    return subprocess.run(["sudo", "sed", "-n", "s/^DATABASE_URL=//p",
                           "/home/vadym/mexc-db-credentials.env"],
                          capture_output=True, text=True, check=True
                          ).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


def max_notional_within(levels, t_bps):
    """levels: [(price, size_usd)] best-first. Returns max USD absorbable
    with VWAP slippage from the touch <= t_bps, and whether the book ran out."""
    if not levels:
        return 0.0, True
    p1 = levels[0][0]
    if p1 <= 0:
        return 0.0, True
    t = t_bps / 1e4
    S = 0.0   # accumulated slippage-weighted notional
    N = 0.0   # accumulated notional
    for p, v in levels:
        if v is None or v <= 0:
            continue
        f = abs(p - p1) / p1
        if f <= t:
            S += v * f
            N += v
            continue
        # this level breaches t -> take the exact partial amount
        denom = f - t
        x = (t * N - S) / denom if denom > 0 else 0.0
        x = max(0.0, min(x, v))
        N += x
        return N, False
    return N, True        # ran out of book within the top-10


def vwap_slip_bps(levels, notional_usd):
    """Slippage from touch, in bps, for consuming `notional_usd`.
    Returns None if the top-10 book cannot absorb it."""
    if not levels:
        return None
    p1 = levels[0][0]
    if p1 <= 0:
        return None
    need = notional_usd
    cost = 0.0
    for p, v in levels:
        if v is None or v <= 0:
            continue
        take = min(need, v)
        cost += take * (abs(p - p1) / p1)
        need -= take
        if need <= 1e-9:
            return cost / notional_usd * 1e4
    return None           # book exhausted


async def main():
    conn = await asyncpg.connect(dsn(), command_timeout=900)
    meta = await conn.fetchrow(
        "SELECT min(ts) t0, max(ts) t1, count(*) n FROM carry_book_l2")
    span_h = (meta["t1"] - meta["t0"]).total_seconds() / 3600.0
    print(f"[i] carry_book_l2: {meta['n']} rows, {meta['t0']} -> {meta['t1']} "
          f"({span_h:.2f} h)", file=sys.stderr)

    rows = await conn.fetch("""
        SELECT exchange, symbol, market, side, ts, level, price, size_usd
        FROM carry_book_l2 ORDER BY exchange, symbol, market, side, ts, level""")
    await conn.close()

    books = defaultdict(list)     # (ex,sym,mkt,side,ts) -> [(price,size_usd)]
    for r in rows:
        books[(r["exchange"], r["symbol"], r["market"], r["side"], r["ts"])].append(
            (r["price"], r["size_usd"]))

    # per (ex,sym,mkt,side): list of per-snapshot capacities and level lists
    cap = defaultdict(lambda: defaultdict(list))
    snaps = defaultdict(list)
    for (ex, sym, mkt, side, ts), lv in books.items():
        lv = [(p, v) for p, v in lv if p and v is not None]
        if len(lv) < 3:
            continue
        snaps[(ex, sym, mkt, side)].append(lv)
        for t in THRESH_BPS:
            n, ran_out = max_notional_within(lv, t)
            cap[(ex, sym, mkt, side)][t].append(n)
        cap[(ex, sym, mkt, side)]["top10"].append(sum(v for _, v in lv))

    def pct(xs, q):
        if not xs:
            return float("nan")
        s = sorted(xs)
        return s[max(0, min(len(s) - 1, int(q * (len(s) - 1))))]

    print("\n" + "=" * 132)
    print(f"STEP 1 — FOUR-LEG DEPTH  (USD absorbable within slippage-from-touch; "
          f"window {span_h:.2f} h — NOT a worst-hour sample)")
    print("=" * 132)
    print(f"{'venue/symbol':<18} {'leg':<18} {'med@10bp':>10} {'p5@10bp':>9} "
          f"{'med@25bp':>10} {'p5@25bp':>9} {'med_top10':>10} {'snaps':>6}")
    depth = {}
    for ex, sym in BASKET:
        for mkt, side, label in LEGS:
            k = (ex, sym, mkt, side)
            c = cap.get(k)
            if not c:
                print(f"{ex+' '+sym:<18} {label:<18} {'NO DATA':>10}")
                continue
            m10, p510 = statistics.median(c[10.0]), pct(c[10.0], 0.05)
            m25, p525 = statistics.median(c[25.0]), pct(c[25.0], 0.05)
            mt = statistics.median(c["top10"])
            depth[k] = {"m10": m10, "p510": p510, "m25": m25, "p525": p525, "top10": mt}
            print(f"{ex+' '+sym:<18} {label:<18} {m10:>10,.0f} {p510:>9,.0f} "
                  f"{m25:>10,.0f} {p525:>9,.0f} {mt:>10,.0f} {len(c[10.0]):>6}")

    print("\n=== BINDING LEG per name (worst of the four, median @25bps) ===")
    print(f"{'venue/symbol':<18} {'binding leg':<18} {'med@25bp $':>12} {'p5@25bp $':>11} "
          f"{'~EUR @25bp':>11}")
    binding = {}
    for ex, sym in BASKET:
        best = None
        for mkt, side, label in LEGS:
            d = depth.get((ex, sym, mkt, side))
            if not d:
                continue
            if best is None or d["m25"] < best[1]["m25"]:
                best = (label, d)
        if best:
            binding[(ex, sym)] = best
            print(f"{ex+' '+sym:<18} {best[0]:<18} {best[1]['m25']:>12,.0f} "
                  f"{best[1]['p525']:>11,.0f} {best[1]['m25']/EURUSD:>11,.0f}")

    # ---------- Step 2: round-trip slippage at real sizes ----------
    print("\n" + "=" * 132)
    print(f"STEP 2 — ROUND-TRIP SLIPPAGE (bps of notional, all 4 legs, from touch) "
          f"| EUR->USD @ {EURUSD}")
    print("=" * 132)
    print(f"{'venue/symbol':<18} " + "  ".join(
        f"{'EUR'+str(s):>9} {'(p5)':>8}" for s in SIZES_EUR))
    rt = {}
    for ex, sym in BASKET:
        cells = []
        for eur in SIZES_EUR:
            usd = eur * EURUSD
            tot_med = 0.0
            tot_p5 = 0.0
            broke_med = broke_p5 = False
            for mkt, side, label in LEGS:
                lv_list = snaps.get((ex, sym, mkt, side), [])
                if not lv_list:
                    broke_med = broke_p5 = True
                    break
                vals = [vwap_slip_bps(lv, usd) for lv in lv_list]
                ok = [v for v in vals in [vals] for v in vals if v is not None] if False else [v for v in vals if v is not None]
                frac_ok = len(ok) / len(vals)
                if frac_ok < 0.5:
                    broke_med = True
                if frac_ok < 0.95:
                    broke_p5 = True
                if ok:
                    tot_med += statistics.median(ok)
                    tot_p5 += pct(ok, 0.95)
            rt[(ex, sym, eur)] = (None if broke_med else tot_med,
                                  None if broke_p5 else tot_p5)
            m, p = rt[(ex, sym, eur)]
            cells.append(f"{('BOOK OUT' if m is None else f'{m:9.1f}'):>9} "
                         f"{('  --' if p is None else f'{p:8.1f}'):>8}")
        print(f"{ex+' '+sym:<18} " + "  ".join(cells))
    print("  'BOOK OUT' = the visible top-10 book cannot absorb that size on at least one leg")
    print("  (p5) = 95th-pct-worst snapshot in this short window, NOT a worst hour")

    # ---------- Step 3: capacity-adjusted net APR ----------
    print("\n" + "=" * 132)
    print(f"STEP 3 — CAPACITY-ADJUSTED NET APR on deployed capital "
          f"(L={LEV:.0f}x, taker-at-size)")
    print("=" * 132)
    print(f"{'venue/symbol':<18} {'gross':>7} " + "  ".join(
        f"| EUR{s} H7 / H30" for s in SIZES_EUR))
    rank = {}
    for ex, sym in BASKET:
        pa = PHASE_A[(ex, sym)]
        cells = []
        for eur in SIZES_EUR:
            m, _ = rt[(ex, sym, eur)]
            if m is None:
                cells.append("|   BOOK OUT      ")
                rank.setdefault((ex, sym), {})[eur] = None
                continue
            tot_bps = pa["rt"] + m
            vals = []
            for H in HOLDS:
                net = (pa["gross"] - tot_bps * (365.0 / H) / 100.0) / (1 + 1 / LEV)
                vals.append(net)
            rank.setdefault((ex, sym), {})[eur] = vals
            cells.append(f"| {vals[0]:>7.1f} /{vals[1]:>7.1f}")
        print(f"{ex+' '+sym:<18} {pa['gross']:>7.1f} " + "  ".join(cells))

    json.dump({f"{k[0]}/{k[1]}": v for k, v in rank.items()},
              open("capacity_rank.json", "w"), default=str)
    json.dump({f"{k[0]}/{k[1]}/{k[2]}/{k[3]}": v for k, v in depth.items()},
              open("capacity_depth.json", "w"), default=str)

    # ---------- Step 4: max prudent size ----------
    print("\n" + "=" * 132)
    print("STEP 4 — MAX PRUDENT SIZE (largest EUR keeping round-trip slippage < 25 bps, "
          "median book)")
    print("=" * 132)
    print(f"{'venue/symbol':<18} {'max EUR':>9} {'rt_slip':>8} {'netAPR@H30':>11} "
          f"{'binding leg':<18}")
    out = []
    for ex, sym in BASKET:
        pa = PHASE_A[(ex, sym)]
        best_eur, best_slip = 0, 0.0
        for eur in range(100, 30100, 100):
            usd = eur * EURUSD
            tot = 0.0
            ok = True
            for mkt, side, label in LEGS:
                lv_list = snaps.get((ex, sym, mkt, side), [])
                if not lv_list:
                    ok = False
                    break
                vals = [vwap_slip_bps(lv, usd) for lv in lv_list]
                good = [v for v in vals if v is not None]
                if len(good) / len(vals) < 0.5:
                    ok = False
                    break
                tot += statistics.median(good)
            if not ok or tot >= 25.0:
                break
            best_eur, best_slip = eur, tot
        if best_eur:
            net30 = (pa["gross"] - (pa["rt"] + best_slip) * (365.0 / 30) / 100.0) / (1 + 1 / LEV)
        else:
            net30 = float("nan")
        bl = binding.get((ex, sym), ("?", None))[0]
        out.append((ex, sym, best_eur, best_slip, net30, bl))
        print(f"{ex+' '+sym:<18} {best_eur:>9,} {best_slip:>8.1f} {net30:>11.1f} {bl:<18}")

    print("\n=== RE-RANK by capacity-adjusted net APR at max prudent size ===")
    for i, r in enumerate(sorted([o for o in out if o[2] > 0],
                                 key=lambda o: -o[4]), 1):
        print(f"  {i}. {r[0]} {r[1]:<14} EUR{r[2]:>6,} @ {r[3]:.1f}bps slip -> "
              f"net {r[4]:.1f}% APR   (binding: {r[5]})")
    tot_cap = sum(o[2] for o in out)
    print(f"\n  BASKET TOTAL max prudent size (sum of per-name caps): EUR {tot_cap:,}")


asyncio.run(main())
