#!/usr/bin/env python3
"""
Is the cross-venue divergence d_bps a tradeable mean-reverting signal, or is it
(a) a persistent structural basis and/or (b) bid/ask flicker inside a wide book?

Decomposes, per coin:
  mean_d      : persistent structural offset (a real, NON-converging level)
  sd_dev      : std of deviation around that offset (the only thing that can revert)
  halfspread  : (mexc_hs + gate_hs)/2 -- the flicker amplitude floor
  ratio       : sd_dev / halfspread  -> ~1 means the "signal" IS quote flicker
  lag1 autocorr of deviation -> ~0 means white noise (untradeable), ~1 means slow drift
"""
import asyncio, sys, statistics, subprocess
from collections import defaultdict
import asyncpg


def dsn():
    return subprocess.run(
        ["sudo", "sed", "-n", "s/^DATABASE_URL=//p", "/home/vadym/mexc-db-credentials.env"],
        capture_output=True, text=True, check=True).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


def series_for(rows):
    byts = defaultdict(dict)
    for r in rows:
        byts[r["ts"]][r["exchange"]] = r
    ds, hs, mids = [], [], []
    for ts in sorted(byts):
        d = byts[ts]
        if "mexc" not in d or "gate" not in d:
            continue
        m, g = d["mexc"], d["gate"]
        if not (m["perp_bid"] and m["perp_ask"] and g["perp_bid"] and g["perp_ask"]):
            continue
        if m["perp_ask"] <= m["perp_bid"] or g["perp_ask"] <= g["perp_bid"]:
            continue
        mm, mg = (m["perp_bid"] + m["perp_ask"]) / 2, (g["perp_bid"] + g["perp_ask"]) / 2
        if mm <= 0 or mg <= 0:
            continue
        ref = (mm + mg) / 2
        dd = (mm - mg) / ref * 1e4
        if abs(dd) > 1000:
            continue
        ds.append(dd)
        hs.append(((m["perp_ask"] - m["perp_bid"]) / mm + (g["perp_ask"] - g["perp_bid"]) / mg) / 2 / 2 * 1e4)
        mids.append(ref)
    return ds, hs


def autocorr1(x):
    n = len(x)
    if n < 3:
        return float("nan")
    mu = statistics.fmean(x)
    num = sum((x[i] - mu) * (x[i + 1] - mu) for i in range(n - 1))
    den = sum((v - mu) ** 2 for v in x)
    return num / den if den else float("nan")


async def main():
    conn = await asyncpg.connect(dsn())
    syms = [r["symbol"] for r in await conn.fetch(
        "SELECT symbol FROM funding_basis_snapshots GROUP BY symbol HAVING count(DISTINCT exchange)=2")]
    syms = [s for s in syms if s != "EDGE_USDT"]
    res = []
    for idx, sym in enumerate(syms):
        rows = await conn.fetch("""SELECT ts, exchange, perp_bid, perp_ask FROM funding_basis_snapshots
                                   WHERE symbol=$1 AND exchange IN ('mexc','gate') ORDER BY ts""", sym)
        ds, hs = series_for(rows)
        if len(ds) < 200:
            continue
        mu = statistics.fmean(ds)
        dev = [v - mu for v in ds]
        res.append({
            "sym": sym, "n": len(ds), "mean_d": mu, "abs_mean_d": abs(mu),
            "sd_dev": statistics.pstdev(dev), "hs": statistics.fmean(hs),
            "ac1": autocorr1(dev),
        })
        if (idx + 1) % 100 == 0:
            print(f"[i] {idx+1}/{len(syms)}", file=sys.stderr)
    await conn.close()

    for r in res:
        r["ratio"] = r["sd_dev"] / r["hs"] if r["hs"] else float("nan")

    print(f"\n=== PER-COIN DIVERGENCE STRUCTURE (n={len(res)} coins, 5-min snapshots, Jul27-Aug15) ===")
    print(f"median |persistent offset|      : {statistics.median(r['abs_mean_d'] for r in res):8.2f} bps")
    print(f"median sd of deviation          : {statistics.median(r['sd_dev'] for r in res):8.2f} bps")
    print(f"median avg half-spread (1 venue): {statistics.median(r['hs'] for r in res):8.2f} bps")
    print(f"median sd_dev / half-spread     : {statistics.median(r['ratio'] for r in res):8.2f}")
    print(f"median lag-1 autocorr of dev    : {statistics.median(r['ac1'] for r in res):8.3f}")
    print(f"  (autocorr ~0 => white noise/flicker; ~1 => slow persistent drift)")

    big = [r for r in res if r["abs_mean_d"] >= 30]
    print(f"\ncoins whose PERSISTENT offset alone is >=30 bps : {len(big)} / {len(res)} "
          f"({100*len(big)/len(res):.1f}%)")
    print("  -> for these, |d|>=30 entries are structural basis, NOT a convergence trade")

    print(f"\n=== worst offenders: largest persistent offset (never converges to 0) ===")
    print(f"{'symbol':<18} {'n':>6} {'mean_d':>9} {'sd_dev':>8} {'half_spr':>9} {'sd/hs':>7} {'ac1':>7}")
    for r in sorted(res, key=lambda r: -r["abs_mean_d"])[:15]:
        print(f"{r['sym']:<18} {r['n']:>6} {r['mean_d']:>9.1f} {r['sd_dev']:>8.1f} "
              f"{r['hs']:>9.1f} {r['ratio']:>7.2f} {r['ac1']:>7.3f}")

    print(f"\n=== the coins my 'upper bound' liked most ===")
    print(f"{'symbol':<18} {'n':>6} {'mean_d':>9} {'sd_dev':>8} {'half_spr':>9} {'sd/hs':>7} {'ac1':>7}")
    want = ["PIPPIN_USDT", "ZBCN_USDT", "HNT_USDT", "VINE_USDT", "AIN_USDT", "DRIFT_USDT",
            "VELO_USDT", "SFP_USDT", "CELR_USDT", "ONE_USDT", "BNB_USDT", "BTC_USDT", "ETH_USDT"]
    by = {r["sym"]: r for r in res}
    for s in want:
        r = by.get(s)
        if r:
            print(f"{r['sym']:<18} {r['n']:>6} {r['mean_d']:>9.1f} {r['sd_dev']:>8.1f} "
                  f"{r['hs']:>9.1f} {r['ratio']:>7.2f} {r['ac1']:>7.3f}")


asyncio.run(main())
