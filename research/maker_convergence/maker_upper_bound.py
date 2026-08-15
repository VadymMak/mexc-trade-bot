#!/usr/bin/env python3
"""
PERFECT-FILL UPPER BOUND for maker perp-perp convergence (MEXC <-> Gate).

THIS IS NOT A BACKTEST. It assumes every posted limit order fills at the touch
with ZERO adverse selection and ZERO queue wait. Real maker fills are strictly
worse. If this upper bound is negative, the strategy is dead. If positive, it
only means the idea survives long enough to justify a proper tick+tape collector.

Data: funding_basis_snapshots (5-min cadence, per-venue perp_bid/perp_ask +
funding_rate). READ ONLY.
"""
import asyncio, sys, os, subprocess, statistics, json
from collections import defaultdict

import asyncpg

MAKER_FEE_BPS = {"mexc": 1.0, "gate": 2.0}   # 0.01% / 0.02%
ROUND_TRIP_FEE_BPS = 2 * MAKER_FEE_BPS["mexc"] + 2 * MAKER_FEE_BPS["gate"]  # 6.0
MAX_GAP_S = 1200          # >20 min gap = data hole, abandon open trade
MIN_DEPTH_USD = 500.0     # both legs must show some book
SANITY_MAX_D_BPS = 1000.0
EXIT_BAND_BPS = 5.0       # "converged" = |divergence| <= this
FUNDING_FILTER_MULT = 3.0 # collectable spread >= 3x expected funding over hold

K_GRID = [20.0, 30.0, 50.0, 75.0, 100.0, 150.0]
T_GRID = [1.0, 4.0, 8.0, 24.0]


def dsn():
    out = subprocess.run(
        ["sudo", "sed", "-n", "s/^DATABASE_URL=//p", "/home/vadym/mexc-db-credentials.env"],
        capture_output=True, text=True, check=True).stdout.strip()
    return out.replace("postgresql+asyncpg://", "postgresql://")


def build_series(rows):
    """rows ordered by ts; pivot to paired per-ts records."""
    byts = defaultdict(dict)
    for r in rows:
        byts[r["ts"]][r["exchange"]] = r
    series = []
    for ts in sorted(byts):
        d = byts[ts]
        if "mexc" not in d or "gate" not in d:
            continue
        m, g = d["mexc"], d["gate"]
        if not (m["perp_bid"] and m["perp_ask"] and g["perp_bid"] and g["perp_ask"]):
            continue
        if m["perp_ask"] <= m["perp_bid"] or g["perp_ask"] <= g["perp_bid"]:
            continue
        # NOTE: perp_depth5_usd is 100% NULL in this archive -> NO depth/size filter
        # is possible. Every result below assumes unlimited size at the touch.
        mid_m = (m["perp_bid"] + m["perp_ask"]) / 2
        mid_g = (g["perp_bid"] + g["perp_ask"]) / 2
        if mid_m <= 0 or mid_g <= 0:
            continue
        ref = (mid_m + mid_g) / 2
        d_bps = (mid_m - mid_g) / ref * 1e4      # >0 => mexc expensive
        if abs(d_bps) > SANITY_MAX_D_BPS:
            continue
        series.append({
            "ts": ts, "d": d_bps, "ref": ref,
            "mexc_bid": m["perp_bid"], "mexc_ask": m["perp_ask"],
            "gate_bid": g["perp_bid"], "gate_ask": g["perp_ask"],
            "fr_mexc": m["funding_rate"] or 0.0, "fr_gate": g["funding_rate"] or 0.0,
        })
    return series


def simulate(series, K, T_hours):
    """Perfect-maker-fill upper bound. Returns list of trade dicts."""
    trades = []
    i = 0
    n = len(series)
    T_s = T_hours * 3600.0
    while i < n:
        s = series[i]
        if abs(s["d"]) < K:
            i += 1
            continue

        # direction: short the expensive venue, long the cheap venue
        if s["d"] > 0:
            short_ex, long_ex = "mexc", "gate"
            sell_entry, buy_entry = s["mexc_ask"], s["gate_bid"]
            fr_short, fr_long = s["fr_mexc"], s["fr_gate"]
        else:
            short_ex, long_ex = "gate", "mexc"
            sell_entry, buy_entry = s["gate_ask"], s["mexc_bid"]
            fr_short, fr_long = s["fr_gate"], s["fr_mexc"]

        ref0 = s["ref"]
        collectable_bps = (sell_entry - buy_entry) / ref0 * 1e4   # |d| + both half-spreads
        exp_fund_bps = abs(fr_short - fr_long) * (T_hours / 8.0) * 1e4
        if collectable_bps < FUNDING_FILTER_MULT * exp_fund_bps:
            i += 1
            continue
        if collectable_bps <= 0:
            i += 1
            continue

        sign = 1.0 if s["d"] > 0 else -1.0
        funding_bps = 0.0
        j = i
        exit_reason = None
        while j + 1 < n:
            cur, nxt = series[j], series[j + 1]
            dt = (nxt["ts"] - cur["ts"]).total_seconds()
            if dt > MAX_GAP_S:
                exit_reason = "gap"
                break
            frs = cur["fr_mexc"] if short_ex == "mexc" else cur["fr_gate"]
            frl = cur["fr_mexc"] if long_ex == "mexc" else cur["fr_gate"]
            funding_bps += (frs - frl) * (dt / 8.0 / 3600.0) * 1e4
            j += 1
            held = (series[j]["ts"] - s["ts"]).total_seconds()
            if series[j]["d"] * sign <= EXIT_BAND_BPS:
                exit_reason = "converged"
                break
            if held >= T_s:
                exit_reason = "timeout"
                break
        else:
            exit_reason = "eod"

        if exit_reason in ("gap", "eod"):
            i = j + 1
            continue

        e = series[j]
        # exit MAKER: sell the long leg at its ask, buy back the short leg at its bid
        if short_ex == "mexc":
            buy_exit, sell_exit = e["mexc_bid"], e["gate_ask"]
        else:
            buy_exit, sell_exit = e["gate_bid"], e["mexc_ask"]

        gross_bps = ((sell_exit - buy_entry) + (sell_entry - buy_exit)) / ref0 * 1e4
        net_bps = gross_bps + funding_bps - ROUND_TRIP_FEE_BPS
        held_h = (e["ts"] - s["ts"]).total_seconds() / 3600.0

        trades.append({
            "entry_ts": s["ts"], "exit_ts": e["ts"], "held_h": held_h,
            "d_entry": s["d"], "d_exit": e["d"], "collectable": collectable_bps,
            "gross": gross_bps, "funding": funding_bps, "net": net_bps,
            "reason": exit_reason, "short_ex": short_ex,
        })
        i = j + 1
    return trades


def agg(trades):
    if not trades:
        return None
    nets = sorted(t["net"] for t in trades)
    return {
        "n": len(trades),
        "win": 100.0 * sum(1 for t in trades if t["net"] > 0) / len(trades),
        "mean": statistics.fmean(nets),
        "median": statistics.median(nets),
        "p10": nets[int(0.10 * (len(nets) - 1))],
        "p90": nets[int(0.90 * (len(nets) - 1))],
        "total": sum(nets),
        "gross": statistics.fmean([t["gross"] for t in trades]),
        "funding": statistics.fmean([t["funding"] for t in trades]),
        "held": statistics.median([t["held_h"] for t in trades]),
        "conv": 100.0 * sum(1 for t in trades if t["reason"] == "converged") / len(trades),
    }


async def main():
    conn = await asyncpg.connect(dsn())
    syms = [r["symbol"] for r in await conn.fetch("""
        SELECT symbol FROM funding_basis_snapshots
        GROUP BY symbol HAVING count(DISTINCT exchange) = 2
    """)]
    syms = [s for s in syms if s != "EDGE_USDT"]
    print(f"[i] {len(syms)} dual-venue symbols", file=sys.stderr)

    all_trades = {(K, T): [] for K in K_GRID for T in T_GRID}
    per_coin = defaultdict(dict)
    kept = 0
    for idx, sym in enumerate(syms):
        rows = await conn.fetch("""
            SELECT ts, exchange, perp_bid, perp_ask, funding_rate, perp_depth5_usd
            FROM funding_basis_snapshots
            WHERE symbol = $1 AND exchange IN ('mexc','gate')
            ORDER BY ts
        """, sym)
        series = build_series(rows)
        if len(series) < 50:
            continue
        kept += 1
        for K in K_GRID:
            for T in T_GRID:
                tr = simulate(series, K, T)
                if tr:
                    all_trades[(K, T)].extend(tr)
                    per_coin[(K, T)][sym] = tr
        if (idx + 1) % 50 == 0:
            print(f"[i] {idx+1}/{len(syms)}", file=sys.stderr)
    await conn.close()
    print(f"[i] {kept} symbols with usable paired series", file=sys.stderr)

    out = {"sweep": {}, "per_coin": {}}
    print("\n=== PERFECT-FILL UPPER BOUND SWEEP (bps per trade, net of 6bps maker fees + funding) ===")
    print(f"{'K':>6} {'T(h)':>5} {'n':>7} {'win%':>6} {'mean':>8} {'med':>8} {'p10':>8} {'p90':>8} "
          f"{'gross':>8} {'fund':>7} {'held_h':>7} {'conv%':>6}")
    for K in K_GRID:
        for T in T_GRID:
            a = agg(all_trades[(K, T)])
            if not a:
                print(f"{K:>6.0f} {T:>5.0f} {0:>7}")
                continue
            out["sweep"][f"K{K:.0f}_T{T:.0f}"] = a
            print(f"{K:>6.0f} {T:>5.0f} {a['n']:>7} {a['win']:>6.1f} {a['mean']:>8.2f} "
                  f"{a['median']:>8.2f} {a['p10']:>8.1f} {a['p90']:>8.1f} {a['gross']:>8.2f} "
                  f"{a['funding']:>7.2f} {a['held']:>7.2f} {a['conv']:>6.1f}")

    # best config by total bps
    best = max((k for k in out["sweep"]), key=lambda k: out["sweep"][k]["mean"] * out["sweep"][k]["n"])
    K, T = float(best.split("_")[0][1:]), float(best.split("_")[1][1:])
    print(f"\n=== PER-COIN, best-by-total config K={K:.0f} T={T:.0f}h (top/bottom 15 by total bps) ===")
    rows = []
    for sym, tr in per_coin[(K, T)].items():
        a = agg(tr)
        rows.append((sym, a))
    rows.sort(key=lambda r: -r[1]["mean"] * r[1]["n"])
    print(f"{'symbol':<18} {'n':>5} {'win%':>6} {'mean':>8} {'total':>10} {'gross':>8} {'fund':>7}")
    for sym, a in rows[:15] + [("...", None)] + rows[-15:]:
        if a is None:
            print("...")
            continue
        print(f"{sym:<18} {a['n']:>5} {a['win']:>6.1f} {a['mean']:>8.2f} {a['total']:>10.1f} "
              f"{a['gross']:>8.2f} {a['funding']:>7.2f}")
    out["per_coin"][best] = {s: a for s, a in rows}
    out["best"] = best
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "upper_bound.json"), "w") as f:
        json.dump(out, f, default=str, indent=1)


asyncio.run(main())
