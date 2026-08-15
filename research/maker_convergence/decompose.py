#!/usr/bin/env python3
"""
Decompose the perfect-fill upper bound into:
  (a) MID-TO-MID convergence  -> the real economic signal (d_entry - d_exit)
  (b) SPREAD CAPTURE          -> pure artifact of assuming free fills at the touch
gross = (a) + (b)

If (b) dominates, the entire "edge" is the fill assumption, not convergence,
and this archive cannot answer the maker question.
"""
import asyncio, sys, statistics, subprocess
from collections import defaultdict
import asyncpg

MAKER_FEE_BPS = {"mexc": 1.0, "gate": 2.0}
ROUND_TRIP_FEE_BPS = 6.0
MAX_GAP_S = 1200
SANITY_MAX_D_BPS = 1000.0
EXIT_BAND_BPS = 5.0
FUNDING_FILTER_MULT = 3.0
CONFIGS = [(20.0, 4.0), (30.0, 8.0), (50.0, 8.0), (100.0, 24.0)]


def dsn():
    return subprocess.run(
        ["sudo", "sed", "-n", "s/^DATABASE_URL=//p", "/home/vadym/mexc-db-credentials.env"],
        capture_output=True, text=True, check=True).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


def build_series(rows):
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
        mid_m, mid_g = (m["perp_bid"] + m["perp_ask"]) / 2, (g["perp_bid"] + g["perp_ask"]) / 2
        if mid_m <= 0 or mid_g <= 0:
            continue
        ref = (mid_m + mid_g) / 2
        dd = (mid_m - mid_g) / ref * 1e4
        if abs(dd) > SANITY_MAX_D_BPS:
            continue
        out.append({"ts": ts, "d": dd, "ref": ref,
                    "mexc_bid": m["perp_bid"], "mexc_ask": m["perp_ask"],
                    "gate_bid": g["perp_bid"], "gate_ask": g["perp_ask"],
                    "sp_m": (m["perp_ask"] - m["perp_bid"]) / mid_m * 1e4,
                    "sp_g": (g["perp_ask"] - g["perp_bid"]) / mid_g * 1e4,
                    "fr_mexc": m["funding_rate"] or 0.0, "fr_gate": g["funding_rate"] or 0.0})
    return out


def simulate(series, K, T_hours):
    trades, i, n, T_s = [], 0, len(series), T_hours * 3600.0
    while i < n:
        s = series[i]
        if abs(s["d"]) < K:
            i += 1
            continue
        if s["d"] > 0:
            short_ex = "mexc"
            sell_entry, buy_entry = s["mexc_ask"], s["gate_bid"]
            fr_s, fr_l = s["fr_mexc"], s["fr_gate"]
        else:
            short_ex = "gate"
            sell_entry, buy_entry = s["gate_ask"], s["mexc_bid"]
            fr_s, fr_l = s["fr_gate"], s["fr_mexc"]
        ref0 = s["ref"]
        collectable = (sell_entry - buy_entry) / ref0 * 1e4
        if collectable <= 0 or collectable < FUNDING_FILTER_MULT * abs(fr_s - fr_l) * (T_hours / 8.0) * 1e4:
            i += 1
            continue
        sign = 1.0 if s["d"] > 0 else -1.0
        funding_bps, j, reason = 0.0, i, None
        while j + 1 < n:
            cur, nxt = series[j], series[j + 1]
            dt = (nxt["ts"] - cur["ts"]).total_seconds()
            if dt > MAX_GAP_S:
                reason = "gap"
                break
            frs = cur["fr_mexc"] if short_ex == "mexc" else cur["fr_gate"]
            frl = cur["fr_gate"] if short_ex == "mexc" else cur["fr_mexc"]
            funding_bps += (frs - frl) * (dt / 8.0 / 3600.0) * 1e4
            j += 1
            if series[j]["d"] * sign <= EXIT_BAND_BPS:
                reason = "converged"
                break
            if (series[j]["ts"] - s["ts"]).total_seconds() >= T_s:
                reason = "timeout"
                break
        else:
            reason = "eod"
        if reason in ("gap", "eod"):
            i = j + 1
            continue
        e = series[j]
        if short_ex == "mexc":
            buy_exit, sell_exit = e["mexc_bid"], e["gate_ask"]
        else:
            buy_exit, sell_exit = e["gate_bid"], e["mexc_ask"]
        gross = ((sell_exit - buy_entry) + (sell_entry - buy_exit)) / ref0 * 1e4
        # (a) mid-to-mid convergence: signed divergence collapse, fills AT MID
        mid_conv = (abs(s["d"]) - e["d"] * sign)
        # (b) spread capture artifact
        spread_cap = gross - mid_conv
        trades.append({
            "gross": gross, "mid_conv": mid_conv, "spread_cap": spread_cap,
            "funding": funding_bps,
            "net_full": gross + funding_bps - ROUND_TRIP_FEE_BPS,
            "net_mid": mid_conv + funding_bps - ROUND_TRIP_FEE_BPS,
            "avg_spread": (s["sp_m"] + s["sp_g"]) / 2,
        })
        i = j + 1
    return trades


async def main():
    conn = await asyncpg.connect(dsn())
    syms = [r["symbol"] for r in await conn.fetch(
        "SELECT symbol FROM funding_basis_snapshots GROUP BY symbol HAVING count(DISTINCT exchange)=2")]
    syms = [s for s in syms if s != "EDGE_USDT"]
    store = {c: [] for c in CONFIGS}
    percoin = {c: defaultdict(list) for c in CONFIGS}
    for idx, sym in enumerate(syms):
        rows = await conn.fetch("""SELECT ts, exchange, perp_bid, perp_ask, funding_rate
                                   FROM funding_basis_snapshots
                                   WHERE symbol=$1 AND exchange IN ('mexc','gate') ORDER BY ts""", sym)
        series = build_series(rows)
        if len(series) < 50:
            continue
        for c in CONFIGS:
            tr = simulate(series, *c)
            store[c].extend(tr)
            if tr:
                percoin[c][sym] = tr
        if (idx + 1) % 100 == 0:
            print(f"[i] {idx+1}/{len(syms)}", file=sys.stderr)
    await conn.close()

    print("\n=== DECOMPOSITION: where does the 'edge' actually come from? ===")
    print(f"{'K':>5} {'T':>4} {'n':>7} | {'gross':>8} {'= mid_conv':>11} {'+ spread_cap':>13} "
          f"| {'%from spread':>12} | {'net_FULL':>9} {'net_MIDFILL':>12} {'win_mid%':>9}")
    for c in CONFIGS:
        tr = store[c]
        if not tr:
            continue
        g = statistics.fmean(t["gross"] for t in tr)
        mc = statistics.fmean(t["mid_conv"] for t in tr)
        sc = statistics.fmean(t["spread_cap"] for t in tr)
        nf = statistics.fmean(t["net_full"] for t in tr)
        nm = statistics.fmean(t["net_mid"] for t in tr)
        wm = 100.0 * sum(1 for t in tr if t["net_mid"] > 0) / len(tr)
        print(f"{c[0]:>5.0f} {c[1]:>4.0f} {len(tr):>7} | {g:>8.2f} {mc:>11.2f} {sc:>13.2f} "
              f"| {100*sc/g if g else 0:>11.1f}% | {nf:>9.2f} {nm:>12.2f} {wm:>8.1f}%")

    print("\n=== Is the 'edge' just the quoted spread? (K=30,T=8; coins bucketed by avg quoted spread) ===")
    c = (30.0, 8.0)
    buckets = defaultdict(list)
    for sym, tr in percoin[c].items():
        s = statistics.fmean(t["avg_spread"] for t in tr)
        b = ("<5bps" if s < 5 else "5-15" if s < 15 else "15-40" if s < 40 else ">40bps")
        buckets[b].extend(tr)
    print(f"{'spread bucket':<14} {'n':>7} {'gross':>8} {'mid_conv':>9} {'spread_cap':>11} "
          f"{'net_FULL':>9} {'net_MIDFILL':>12} {'win_mid%':>9}")
    for b in ["<5bps", "5-15", "15-40", ">40bps"]:
        tr = buckets.get(b)
        if not tr:
            continue
        print(f"{b:<14} {len(tr):>7} {statistics.fmean(t['gross'] for t in tr):>8.2f} "
              f"{statistics.fmean(t['mid_conv'] for t in tr):>9.2f} "
              f"{statistics.fmean(t['spread_cap'] for t in tr):>11.2f} "
              f"{statistics.fmean(t['net_full'] for t in tr):>9.2f} "
              f"{statistics.fmean(t['net_mid'] for t in tr):>12.2f} "
              f"{100.0*sum(1 for t in tr if t['net_mid']>0)/len(tr):>8.1f}%")


asyncio.run(main())
