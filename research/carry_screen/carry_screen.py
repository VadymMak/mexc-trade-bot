#!/usr/bin/env python3
"""
CARRY CANDIDATE SCREEN — honest, read-only.

Strategy: delta-neutral funding carry = LONG spot + SHORT perp (collect positive
funding), or the inverse when funding is persistently negative.

Heavy aggregation is done in SQL (6.5M rows); Python only handles the reduced
per-epoch funding series and the scoring.

KEY DATA CAVEAT baked in everywhere: the collector HARDCODES
FUNDING_INTERVAL_HOURS = 8 for both venues (researcher/app/carry/main.py).
funding_interval_hours in the table is therefore NOT venue truth. Any coin whose
real interval is 4h or 1h has its APR UNDERSTATED here by 2x or 8x. Treat every
APR as "at an assumed 8h interval, to be verified per coin".
"""
import asyncio, math, statistics, subprocess, sys, json
from collections import defaultdict
import asyncpg

# fee assumptions (bps per fill) -- STATE, do not hide
MAKER = {"mexc": 1.0, "gate": 2.0}     # per user: MEXC 0.01% API, Gate 0.02%
TAKER = {"mexc": 5.0, "gate": 5.0}     # typical retail taker 0.05% -- VERIFY
HOLDS = [1, 3, 7, 30]                  # days


def dsn():
    return subprocess.run(["sudo", "sed", "-n", "s/^DATABASE_URL=//p",
                           "/home/vadym/mexc-db-credentials.env"],
                          capture_output=True, text=True, check=True
                          ).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


SQL_AGG = """
SELECT exchange, symbol,
  count(*) n,
  min(ts) t0, max(ts) t1,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY perp_spread_bps) perp_spr_med,
  percentile_cont(0.9) WITHIN GROUP (ORDER BY perp_spread_bps) perp_spr_p90,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY spot_spread_bps) spot_spr_med,
  percentile_cont(0.9) WITHIN GROUP (ORDER BY spot_spread_bps) spot_spr_p90,
  avg(basis_bps) basis_mean, stddev_pop(basis_bps) basis_sd,
  avg(perp_mark) px
FROM funding_basis_snapshots
WHERE perp_bid > 0 AND perp_ask > perp_bid AND spot_bid > 0 AND spot_ask > spot_bid
GROUP BY 1,2
"""

# lag-1day (288 snapshots @5min) autocorrelation of basis -> persistent vs mean-reverting
SQL_AC = """
WITH d AS (
  SELECT exchange, symbol, basis_bps,
         lag(basis_bps, 288) OVER (PARTITION BY exchange, symbol ORDER BY ts) b1d,
         lag(basis_bps, 12)  OVER (PARTITION BY exchange, symbol ORDER BY ts) b1h
  FROM funding_basis_snapshots)
SELECT exchange, symbol,
       corr(basis_bps, b1d) ac_1d,
       corr(basis_bps, b1h) ac_1h
FROM d WHERE b1d IS NOT NULL AND b1h IS NOT NULL
GROUP BY 1,2
"""

# realized funding per 8h settlement epoch = last snapshot before the boundary
SQL_EPOCH = """
SELECT DISTINCT ON (exchange, symbol, ep)
       exchange, symbol,
       (floor(extract(epoch FROM ts)/28800))::bigint ep,
       funding_rate
FROM funding_basis_snapshots
WHERE funding_rate IS NOT NULL
ORDER BY exchange, symbol, ep, ts DESC
"""

SQL_GAP = """
WITH d AS (SELECT exchange, symbol, ts,
                  lag(ts) OVER (PARTITION BY exchange, symbol ORDER BY ts) p
           FROM funding_basis_snapshots)
SELECT exchange, symbol, max(extract(epoch FROM (ts - p)))/60.0 max_gap_min
FROM d WHERE p IS NOT NULL GROUP BY 1,2
"""


async def main():
    conn = await asyncpg.connect(dsn(), command_timeout=1800)
    print("[i] aggregates…", file=sys.stderr)
    agg = {(r["exchange"], r["symbol"]): dict(r) for r in await conn.fetch(SQL_AGG)}
    print(f"[i]   {len(agg)} coin-venues", file=sys.stderr)
    print("[i] basis autocorrelation…", file=sys.stderr)
    ac = {(r["exchange"], r["symbol"]): dict(r) for r in await conn.fetch(SQL_AC)}
    print("[i] realized funding epochs…", file=sys.stderr)
    eps = await conn.fetch(SQL_EPOCH)
    print("[i] gaps…", file=sys.stderr)
    gaps = {(r["exchange"], r["symbol"]): r["max_gap_min"] for r in await conn.fetch(SQL_GAP)}
    await conn.close()

    series = defaultdict(list)
    for r in eps:
        series[(r["exchange"], r["symbol"])].append((r["ep"], r["funding_rate"]))

    rows = []
    for key, a in agg.items():
        ex, sym = key
        s = sorted(series.get(key, []))
        if len(s) < 20:
            continue
        rates = [v for _, v in s]
        n_ep = len(rates)
        mean_r = statistics.fmean(rates)
        sd_r = statistics.pstdev(rates)
        pos = 100.0 * sum(1 for v in rates if v > 0) / n_ep
        # sign flips between consecutive epochs, per week
        flips = sum(1 for i in range(1, n_ep)
                    if (rates[i] > 0) != (rates[i - 1] > 0))
        days = (a["t1"] - a["t0"]).total_seconds() / 86400.0
        flips_wk = flips / days * 7.0 if days > 0 else float("nan")
        apr = mean_r * 3.0 * 365.0 * 100.0          # 8h interval assumption
        # direction: if funding persistently negative we run the INVERSE leg
        direction = "long-spot/short-perp" if mean_r >= 0 else "short-spot/long-perp"
        apr_abs = abs(apr)
        pos_eff = pos if mean_r >= 0 else 100.0 - pos

        perp_spr = a["perp_spr_med"] or float("nan")
        spot_spr = a["spot_spr_med"] or float("nan")
        # round trip crossing both legs at entry AND exit = full spread on each leg
        cross_bps = perp_spr + spot_spr
        fee_taker = 4 * TAKER[ex]
        fee_maker = 4 * MAKER[ex]
        rt_taker = cross_bps + fee_taker
        rt_maker = fee_maker                      # maker: post both legs, pay no spread

        net = {}
        for H in HOLDS:
            net[f"taker_H{H}"] = apr_abs - rt_taker * (365.0 / H) / 100.0
            net[f"maker_H{H}"] = apr_abs - rt_maker * (365.0 / H) / 100.0

        acr = ac.get(key, {})
        rows.append({
            "ex": ex, "sym": sym, "n": a["n"], "n_ep": n_ep, "px": a["px"],
            "apr": apr, "apr_abs": apr_abs, "dir": direction,
            "mean_r": mean_r, "sd_r": sd_r, "pos": pos, "pos_eff": pos_eff,
            "flips_wk": flips_wk, "min_r": min(rates), "max_r": max(rates),
            "perp_spr": perp_spr, "spot_spr": spot_spr,
            "perp_spr90": a["perp_spr_p90"], "spot_spr90": a["spot_spr_p90"],
            "basis_mean": a["basis_mean"], "basis_sd": a["basis_sd"],
            "ac_1d": acr.get("ac_1d"), "ac_1h": acr.get("ac_1h"),
            "rt_taker": rt_taker, "rt_maker": rt_maker,
            "max_gap_min": gaps.get(key, float("nan")),
            "days": days, **net,
        })

    with open("carry_rows.json", "w") as f:
        json.dump(rows, f, default=str)
    print(f"[i] wrote {len(rows)} rows to carry_rows.json", file=sys.stderr)

    # ---------- gates ----------
    def passes(r):
        return (r["pos_eff"] >= 80.0                      # funding consistently one sign
                and r["flips_wk"] <= 1.0                  # rarely reverses
                and r["perp_spr"] <= 15.0                 # perp leg tradeable
                and r["spot_spr"] <= 20.0                 # spot leg tradeable
                and abs(r["basis_mean"]) <= 100.0         # not a broken basis
                and (r["ac_1d"] is None or r["ac_1d"] <= 0.85)   # mean-reverting, not stuck
                and r["max_gap_min"] <= 180.0             # no long data holes
                and r["taker_H30"] > 0)

    ok = [r for r in rows if passes(r)]
    ok.sort(key=lambda r: -r["taker_H30"])

    print("\n" + "=" * 150)
    print("SHORTLIST — passes ALL stability gates, ranked by NET APR at H=30d (taker costs)")
    print("gates: |funding sign| consistent >=80% of epochs, <=1 flip/wk, perp spr<=15bps, "
          "spot spr<=20bps, |basis|<=100bps, basis ac(1d)<=0.85, no >3h gap, net>0")
    print("=" * 150)
    hdr = (f"{'venue':<5} {'symbol':<16} {'dir':<21} {'grossAPR':>9} {'netH30':>8} {'netH7':>8} "
           f"{'netH3':>8} {'netH1':>9} {'pos%':>6} {'flip/wk':>8} {'pspr':>6} {'sspr':>6} "
           f"{'basis':>8} {'ac1d':>6}")
    print(hdr)
    for r in ok[:30]:
        print(f"{r['ex']:<5} {r['sym']:<16} {r['dir']:<21} {r['apr']:>9.2f} "
              f"{r['taker_H30']:>8.2f} {r['taker_H7']:>8.2f} {r['taker_H3']:>8.2f} "
              f"{r['taker_H1']:>9.2f} {r['pos_eff']:>6.1f} {r['flips_wk']:>8.2f} "
              f"{r['perp_spr']:>6.2f} {r['spot_spr']:>6.2f} {r['basis_mean']:>8.1f} "
              f"{(r['ac_1d'] if r['ac_1d'] is not None else float('nan')):>6.2f}")
    print(f"\n[{len(ok)} of {len(rows)} coin-venues pass all gates]")

    # ---------- traps ----------
    print("\n" + "=" * 150)
    print("TRAPS — highest GROSS APR names and why each fails")
    print("=" * 150)
    hi = sorted(rows, key=lambda r: -r["apr_abs"])[:25]
    print(f"{'venue':<5} {'symbol':<16} {'grossAPR':>9} {'netH30':>8} {'pos%':>6} {'flip/wk':>8} "
          f"{'pspr':>7} {'sspr':>8} {'basis':>9} {'ac1d':>6}  reasons")
    for r in hi:
        why = []
        if r["pos_eff"] < 80:
            why.append(f"sign flips ({r['pos_eff']:.0f}% consistent)")
        if r["flips_wk"] > 1.0:
            why.append(f"{r['flips_wk']:.1f} reversals/wk")
        if r["perp_spr"] > 15:
            why.append(f"perp spread {r['perp_spr']:.0f}bps")
        if r["spot_spr"] > 20:
            why.append(f"spot spread {r['spot_spr']:.0f}bps")
        if abs(r["basis_mean"]) > 100:
            why.append(f"basis {r['basis_mean']:.0f}bps")
        if r["ac_1d"] is not None and r["ac_1d"] > 0.85:
            why.append(f"persistent basis ac1d={r['ac_1d']:.2f}")
        if r["max_gap_min"] > 180:
            why.append(f"gap {r['max_gap_min']/60:.1f}h")
        if r["taker_H30"] <= 0:
            why.append("net<0 even at H=30")
        print(f"{r['ex']:<5} {r['sym']:<16} {r['apr']:>9.1f} {r['taker_H30']:>8.1f} "
              f"{r['pos_eff']:>6.1f} {r['flips_wk']:>8.2f} {r['perp_spr']:>7.1f} "
              f"{r['spot_spr']:>8.1f} {r['basis_mean']:>9.1f} "
              f"{(r['ac_1d'] if r['ac_1d'] is not None else float('nan')):>6.2f}  "
              f"{'; '.join(why) if why else 'PASSES — see shortlist'}")

    # ---------- anchors ----------
    print("\n=== SANITY ANCHORS (majors) ===")
    print(hdr)
    for want in ["BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT"]:
        for r in rows:
            if r["sym"] == want:
                print(f"{r['ex']:<5} {r['sym']:<16} {r['dir']:<21} {r['apr']:>9.2f} "
                      f"{r['taker_H30']:>8.2f} {r['taker_H7']:>8.2f} {r['taker_H3']:>8.2f} "
                      f"{r['taker_H1']:>9.2f} {r['pos_eff']:>6.1f} {r['flips_wk']:>8.2f} "
                      f"{r['perp_spr']:>6.2f} {r['spot_spr']:>6.2f} {r['basis_mean']:>8.1f} "
                      f"{(r['ac_1d'] if r['ac_1d'] is not None else float('nan')):>6.2f}")

    # ---------- maker-vs-taker sensitivity on the shortlist ----------
    print("\n=== COST SENSITIVITY on top 10 shortlist (NET APR %) ===")
    print(f"{'venue/symbol':<22} {'gross':>8} | " +
          " ".join(f"{'tk_H'+str(H):>8}" for H in HOLDS) + " | " +
          " ".join(f"{'mk_H'+str(H):>8}" for H in HOLDS))
    for r in ok[:10]:
        print(f"{r['ex']+' '+r['sym']:<22} {r['apr']:>8.2f} | " +
              " ".join(f"{r['taker_H'+str(H)]:>8.2f}" for H in HOLDS) + " | " +
              " ".join(f"{r['maker_H'+str(H)]:>8.2f}" for H in HOLDS))

    # ---------- distribution ----------
    print("\n=== FUNDING APR DISTRIBUTION (all coin-venues, 8h assumption) ===")
    aprs = sorted(r["apr"] for r in rows)
    n = len(aprs)
    print(f"n={n}  negative={100.0*sum(1 for v in aprs if v<0)/n:.1f}%  "
          f"|APR|<=10%={100.0*sum(1 for v in aprs if abs(v)<=10)/n:.1f}%")
    for q in [0.05, 0.25, 0.5, 0.75, 0.95, 0.99]:
        print(f"  p{int(q*100):>2}: {aprs[int(q*(n-1))]:>8.2f}%")


asyncio.run(main())
