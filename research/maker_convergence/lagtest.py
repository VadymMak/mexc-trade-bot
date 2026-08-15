#!/usr/bin/env python3
"""
NOISE-REVERSION ARTIFACT TEST.

If d_bps is a real, slowly-converging economic basis, delaying execution by one
5-min snapshot should barely change the captured edge.

If d_bps is dominated by measurement noise in the mid (bid/ask flicker inside a
wide book), then selecting on |d|>=K picks observations where the NOISE is large,
and the apparent "convergence" is that noise reverting between t and t+1. In that
case the edge collapses as soon as you execute one snapshot later, because the
artifact has already reverted before you can trade it.

Reports mid-to-mid captured edge at execution lags 0, 1, 2, 3 snapshots.
"""
import asyncio, sys, statistics, subprocess
from collections import defaultdict
import asyncpg

K_GRID = [20.0, 30.0, 50.0, 100.0]
LAGS = [0, 1, 2, 3]
T_HOURS = 8.0
EXIT_BAND = 5.0
FEES = 6.0


def dsn():
    return subprocess.run(
        ["sudo", "sed", "-n", "s/^DATABASE_URL=//p", "/home/vadym/mexc-db-credentials.env"],
        capture_output=True, text=True, check=True).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


def build(rows):
    byts = defaultdict(dict)
    for r in rows:
        byts[r["ts"]][r["exchange"]] = r
    out = []
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
        out.append((ts, dd))
    return out


def run(series, K, lag):
    """mid-to-mid captured convergence, executing `lag` snapshots after the signal."""
    res, i, n = [], 0, len(series)
    while i < n:
        ts, d = series[i]
        if abs(d) < K:
            i += 1
            continue
        j = i + lag
        if j >= n:
            break
        if (series[j][0] - ts).total_seconds() > 1200 * (lag + 1):
            i += 1
            continue
        d_exec = series[j][1]
        sign = 1.0 if d > 0 else -1.0          # direction chosen from the SIGNAL, not the exec price
        entry = d_exec * sign                  # signed divergence we actually get in at
        k = j
        while k + 1 < n:
            if (series[k + 1][0] - series[k][0]).total_seconds() > 1200:
                break
            k += 1
            if series[k][1] * sign <= EXIT_BAND:
                break
            if (series[k][0] - series[j][0]).total_seconds() >= T_HOURS * 3600:
                break
        exitd = series[k][1] * sign
        res.append(entry - exitd)
        i = k + 1
    return res


async def main():
    conn = await asyncpg.connect(dsn())
    syms = [r["symbol"] for r in await conn.fetch(
        "SELECT symbol FROM funding_basis_snapshots GROUP BY symbol HAVING count(DISTINCT exchange)=2")]
    syms = [s for s in syms if s != "EDGE_USDT"]
    acc = {(K, L): [] for K in K_GRID for L in LAGS}
    for idx, sym in enumerate(syms):
        rows = await conn.fetch("""SELECT ts, exchange, perp_bid, perp_ask FROM funding_basis_snapshots
                                   WHERE symbol=$1 AND exchange IN ('mexc','gate') ORDER BY ts""", sym)
        s = build(rows)
        if len(s) < 200:
            continue
        for K in K_GRID:
            for L in LAGS:
                acc[(K, L)].extend(run(s, K, L))
        if (idx + 1) % 100 == 0:
            print(f"[i] {idx+1}/{len(syms)}", file=sys.stderr)
    await conn.close()

    print("\n=== EXECUTION-LAG TEST: mid-to-mid captured convergence (bps, before fees) ===")
    print("   T=8h, exit band 5bps. Lag = snapshots (5 min each) between signal and execution.\n")
    print(f"{'K':>5} | " + " | ".join(f"lag{L} ({'0' if L==0 else L*5}min){'':>2}" for L in LAGS))
    print(f"{'':>5} | " + " | ".join(f"{'n':>7} {'mean':>7} {'net':>7}" for L in LAGS))
    for K in K_GRID:
        cells = []
        for L in LAGS:
            v = acc[(K, L)]
            if not v:
                cells.append(f"{0:>7} {'-':>7} {'-':>7}")
                continue
            m = statistics.fmean(v)
            cells.append(f"{len(v):>7} {m:>7.2f} {m-FEES:>7.2f}")
        print(f"{K:>5.0f} | " + " | ".join(cells))

    print("\n  net = mean captured convergence - 6 bps round-trip maker fees")
    print("  (still assumes PERFECT maker fills at mid on all 4 legs, no adverse selection)")
    for K in K_GRID:
        v0, v1 = acc[(K, 0)], acc[(K, 1)]
        if v0 and v1:
            m0, m1 = statistics.fmean(v0), statistics.fmean(v1)
            print(f"  K={K:>3.0f}: edge decay from lag0 -> lag1 (5 min): "
                  f"{m0:.2f} -> {m1:.2f} bps  ({100*(m1-m0)/m0 if m0 else 0:+.1f}%)")


asyncio.run(main())
