#!/usr/bin/env python3
"""
CARRY SCREEN v2 — adds the three things v1 got wrong or missed.

1. DIRECTION FEASIBILITY. v1 ranked "short-spot/long-perp" names at the top.
   That leg requires BORROWING and SHORT-SELLING spot altcoins, which on
   MEXC/Gate is generally unavailable or expensive for thin alts. Only
   LONG-spot / SHORT-perp is reliably executable with plain spot + perp
   accounts. Negative-funding names are therefore reported separately and
   marked NOT-EXECUTABLE-UNVERIFIED, not ranked as winners.

2. FUNDING DECAY. 19 days is a very short sample and many high-APR names are
   NEW LISTINGS whose funding is elevated during the launch-phase long squeeze
   and then normalises. Compare first-half vs second-half APR per coin; a large
   drop means the trailing APR overstates the forward APR.

3. BROKEN PAIRS. |basis| in the hundreds/thousands of bps means perp and spot
   are not the same asset (mismatched symbol / contract scale). Those are data
   integrity failures, excluded outright rather than listed as "traps".
"""
import asyncio, json, statistics, subprocess, sys
from collections import defaultdict
import asyncpg

MAKER = {"mexc": 1.0, "gate": 2.0}
TAKER = {"mexc": 5.0, "gate": 5.0}
HOLDS = [1, 3, 7, 30]
WINDOW_START = "2026-07-27"


def dsn():
    return subprocess.run(["sudo", "sed", "-n", "s/^DATABASE_URL=//p",
                           "/home/vadym/mexc-db-credentials.env"],
                          capture_output=True, text=True, check=True
                          ).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


SQL_HALVES = """
WITH b AS (SELECT min(ts) t0, max(ts) t1 FROM funding_basis_snapshots),
ep AS (
  SELECT DISTINCT ON (exchange, symbol, (floor(extract(epoch FROM ts)/28800))::bigint)
         exchange, symbol, ts, funding_rate,
         (floor(extract(epoch FROM ts)/28800))::bigint e
  FROM funding_basis_snapshots WHERE funding_rate IS NOT NULL
  ORDER BY exchange, symbol, e, ts DESC)
SELECT ep.exchange, ep.symbol,
  count(*) n_ep,
  min(ep.ts) first_seen,
  avg(ep.funding_rate) FILTER (WHERE ep.ts < b.t0 + (b.t1-b.t0)/2) fr_h1,
  avg(ep.funding_rate) FILTER (WHERE ep.ts >= b.t0 + (b.t1-b.t0)/2) fr_h2,
  count(*) FILTER (WHERE ep.ts < b.t0 + (b.t1-b.t0)/2) n1,
  count(*) FILTER (WHERE ep.ts >= b.t0 + (b.t1-b.t0)/2) n2
FROM ep CROSS JOIN b GROUP BY 1,2
"""


async def main():
    rows = json.load(open("carry_rows.json"))
    for r in rows:
        for k in ("apr", "apr_abs", "perp_spr", "spot_spr", "basis_mean", "pos_eff",
                  "flips_wk", "max_gap_min", "ac_1d", "sd_r", "mean_r"):
            if r.get(k) is not None:
                r[k] = float(r[k])

    conn = await asyncpg.connect(dsn(), command_timeout=1800)
    halves = {(r["exchange"], r["symbol"]): dict(r) for r in await conn.fetch(SQL_HALVES)}
    await conn.close()

    MAX_EP = max(h["n_ep"] for h in halves.values())
    for r in rows:
        h = halves.get((r["ex"], r["sym"]), {})
        r["n_ep"] = h.get("n_ep", 0)
        r["first_seen"] = str(h.get("first_seen", ""))[:10]
        f1, f2 = h.get("fr_h1"), h.get("fr_h2")
        r["apr_h1"] = float(f1) * 3 * 365 * 100 if f1 is not None else None
        r["apr_h2"] = float(f2) * 3 * 365 * 100 if f2 is not None else None
        r["new_listing"] = r["n_ep"] < 0.9 * MAX_EP
        r["broken_pair"] = abs(r["basis_mean"]) > 500.0
        r["executable"] = r["mean_r"] >= 0        # long spot + short perp

    def net(r, H, taker=True):
        rt = r["rt_taker"] if taker else r["rt_maker"]
        return r["apr_abs"] - float(rt) * (365.0 / H) / 100.0

    print("=" * 152)
    print("EXECUTABILITY SPLIT — only LONG-spot/SHORT-perp is reliably doable with plain "
          "spot+perp accounts")
    print("=" * 152)
    ex_ok = [r for r in rows if r["executable"] and not r["broken_pair"]]
    ex_no = [r for r in rows if not r["executable"] and not r["broken_pair"]]
    broken = [r for r in rows if r["broken_pair"]]
    print(f"  long-spot/short-perp (EXECUTABLE)        : {len(ex_ok)}")
    print(f"  short-spot/long-perp (needs spot borrow) : {len(ex_no)}  -> reported separately")
    print(f"  broken pairs (|basis|>500bps, excluded)  : {len(broken)}")
    if broken:
        print("     e.g. " + ", ".join(f"{r['ex']}/{r['sym']}({r['basis_mean']:.0f}bps)"
                                       for r in sorted(broken, key=lambda r: -abs(r["basis_mean"]))[:8]))

    def gates(r):
        return (r["pos_eff"] >= 85.0 and r["flips_wk"] <= 0.75
                and r["perp_spr"] <= 15.0 and r["spot_spr"] <= 20.0
                and abs(r["basis_mean"]) <= 100.0
                and (r["ac_1d"] is None or r["ac_1d"] <= 0.85)
                and r["max_gap_min"] <= 180.0 and net(r, 30) > 0)

    ok = [r for r in ex_ok if gates(r)]
    ok.sort(key=lambda r: -net(r, 30))

    print("\n" + "=" * 152)
    print("SHORTLIST — EXECUTABLE direction only (long spot + short perp), all gates passed, "
          "ranked by NET APR @H=30d taker")
    print("=" * 152)
    print(f"{'venue':<5} {'symbol':<15} {'gross':>7} {'netH30':>7} {'netH7':>7} {'netH3':>7} "
          f"{'pos%':>5} {'fl/wk':>6} {'pspr':>5} {'sspr':>6} {'basis':>7} {'ac1d':>6} "
          f"{'APRh1':>8} {'APRh2':>8} {'decay':>7} {'new?':>5}")
    for r in ok[:25]:
        d = (r["apr_h2"] - r["apr_h1"]) if (r["apr_h1"] is not None and r["apr_h2"] is not None) else float("nan")
        print(f"{r['ex']:<5} {r['sym']:<15} {r['apr']:>7.1f} {net(r,30):>7.1f} {net(r,7):>7.1f} "
              f"{net(r,3):>7.1f} {r['pos_eff']:>5.0f} {r['flips_wk']:>6.2f} {r['perp_spr']:>5.1f} "
              f"{r['spot_spr']:>6.1f} {r['basis_mean']:>7.1f} "
              f"{(r['ac_1d'] if r['ac_1d'] is not None else float('nan')):>6.2f} "
              f"{(r['apr_h1'] if r['apr_h1'] is not None else float('nan')):>8.1f} "
              f"{(r['apr_h2'] if r['apr_h2'] is not None else float('nan')):>8.1f} "
              f"{d:>7.1f} {'NEW' if r['new_listing'] else '-':>5}")
    print(f"\n[{len(ok)} of {len(ex_ok)} executable coin-venues pass all gates]")

    print("\n=== FUNDING DECAY on the shortlist (does trailing APR overstate forward APR?) ===")
    dec = [r for r in ok if r["apr_h1"] is not None and r["apr_h2"] is not None]
    if dec:
        drops = [r["apr_h2"] - r["apr_h1"] for r in dec]
        worse = sum(1 for d in drops if d < 0)
        print(f"  {worse}/{len(dec)} shortlist names had LOWER funding in the 2nd half")
        print(f"  median change: {statistics.median(drops):+.2f} pp APR   "
              f"mean change: {statistics.fmean(drops):+.2f} pp APR")
        print(f"  median APR 1st half {statistics.median(r['apr_h1'] for r in dec):.2f}%  "
              f"-> 2nd half {statistics.median(r['apr_h2'] for r in dec):.2f}%")
        nl = [r for r in ok if r["new_listing"]]
        print(f"  new listings (<90% of epochs present) in shortlist: {len(nl)}/{len(ok)}")

    print("\n" + "=" * 152)
    print("NEGATIVE-FUNDING NAMES — high |APR| but require SHORTING SPOT (borrow). "
          "NOT executable on plain accounts; marked unverified.")
    print("=" * 152)
    ex_no.sort(key=lambda r: -r["apr_abs"])
    print(f"{'venue':<5} {'symbol':<15} {'grossAPR':>9} {'pos%':>5} {'fl/wk':>6} {'sspr':>6} "
          f"{'basis':>8}  status")
    for r in ex_no[:12]:
        print(f"{r['ex']:<5} {r['sym']:<15} {r['apr']:>9.1f} {r['pos_eff']:>5.0f} "
              f"{r['flips_wk']:>6.2f} {r['spot_spr']:>6.1f} {r['basis_mean']:>8.1f}  "
              f"SPOT-BORROW REQUIRED — verify availability/cost before counting this")

    print("\n" + "=" * 152)
    print("TRAPS — tempting high GROSS |APR| that fail the gates (executable direction only)")
    print("=" * 152)
    hi = sorted([r for r in ex_ok if not gates(r)], key=lambda r: -r["apr_abs"])[:18]
    print(f"{'venue':<5} {'symbol':<15} {'gross':>7} {'netH30':>7} {'pos%':>5} {'fl/wk':>6} "
          f"{'pspr':>6} {'sspr':>7} {'basis':>8} {'ac1d':>6}  why excluded")
    for r in hi:
        why = []
        if r["pos_eff"] < 85:
            why.append(f"funding sign only {r['pos_eff']:.0f}% consistent")
        if r["flips_wk"] > 0.75:
            why.append(f"{r['flips_wk']:.1f} reversals/wk")
        if r["perp_spr"] > 15:
            why.append(f"perp spr {r['perp_spr']:.0f}bps")
        if r["spot_spr"] > 20:
            why.append(f"spot spr {r['spot_spr']:.0f}bps")
        if abs(r["basis_mean"]) > 100:
            why.append(f"basis {r['basis_mean']:.0f}bps")
        if r["ac_1d"] is not None and r["ac_1d"] > 0.85:
            why.append(f"persistent basis ac1d={r['ac_1d']:.2f}")
        if r["max_gap_min"] > 180:
            why.append(f"data gap {r['max_gap_min']/60:.1f}h")
        if net(r, 30) <= 0:
            why.append("net<0 @H30")
        print(f"{r['ex']:<5} {r['sym']:<15} {r['apr']:>7.1f} {net(r,30):>7.1f} {r['pos_eff']:>5.0f} "
              f"{r['flips_wk']:>6.2f} {r['perp_spr']:>6.1f} {r['spot_spr']:>7.1f} "
              f"{r['basis_mean']:>8.1f} "
              f"{(r['ac_1d'] if r['ac_1d'] is not None else float('nan')):>6.2f}  "
              f"{'; '.join(why)}")

    print("\n=== SANITY ANCHORS (majors, executable direction) ===")
    print(f"{'venue':<5} {'symbol':<15} {'gross':>7} {'netH30':>7} {'netH7':>7} {'pos%':>5} "
          f"{'fl/wk':>6} {'pspr':>5} {'sspr':>5} {'basis':>7}")
    for want in ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "BNB_USDT"]:
        for r in rows:
            if r["sym"] == want and r["executable"]:
                print(f"{r['ex']:<5} {r['sym']:<15} {r['apr']:>7.2f} {net(r,30):>7.2f} "
                      f"{net(r,7):>7.2f} {r['pos_eff']:>5.0f} {r['flips_wk']:>6.2f} "
                      f"{r['perp_spr']:>5.2f} {r['spot_spr']:>5.2f} {r['basis_mean']:>7.1f}")

    print("\n=== TOP-5 CANDIDATE DETAIL (maker vs taker, all holds) ===")
    print(f"{'venue/symbol':<22} {'gross':>7} | " + " ".join(f"{'tk_H'+str(H):>7}" for H in HOLDS)
          + " | " + " ".join(f"{'mk_H'+str(H):>7}" for H in HOLDS) + " | rt_cost_bps")
    for r in ok[:5]:
        print(f"{r['ex']+' '+r['sym']:<22} {r['apr']:>7.1f} | "
              + " ".join(f"{net(r,H):>7.1f}" for H in HOLDS) + " | "
              + " ".join(f"{net(r,H,False):>7.1f}" for H in HOLDS)
              + f" | taker {float(r['rt_taker']):.1f} / maker {float(r['rt_maker']):.1f}")

    json.dump([{k: v for k, v in r.items()} for r in ok], open("shortlist.json", "w"), default=str)


asyncio.run(main())
