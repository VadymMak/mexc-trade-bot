#!/usr/bin/env python3
"""
PART A — correct the carry economics (read-only).

1. Fetch the REAL funding interval per basket coin:
     Gate : /futures/usdt/contracts/{c}    -> funding_interval (SECONDS)
     MEXC : /contract/funding_rate/{s}     -> collectCycle (HOURS)
   The carry collector hardcodes 8h, so 4h/1h names were understated 2x/8x.

2. Re-extract realized funding epochs at each coin's TRUE interval (not the 8h
   grid), so we are not sampling every other settlement on a 4h coin.

3. Recompute NET APR as return on DEPLOYED CAPITAL, not on notional:
       deployed C = S + S/L          (spot notional + perp margin at leverage L)
       net_on_capital = (gross_APR - rt_bps*(365/H)/100) / (1 + 1/L)
"""
import asyncio, json, statistics, subprocess, sys
from collections import defaultdict
import asyncpg, urllib.request

BASKET = [("gate", "HANA_USDT"), ("gate", "WET_USDT"), ("gate", "IDOL_USDT"),
          ("gate", "BTR_USDT"), ("mexc", "PLAY_USDT"), ("mexc", "BTC_USDT")]
MAKER = {"mexc": 1.0, "gate": 2.0}
TAKER = {"mexc": 5.0, "gate": 5.0}
HOLDS = [3, 7, 30]
LEVS = [1, 2, 3]


def dsn():
    return subprocess.run(["sudo", "sed", "-n", "s/^DATABASE_URL=//p",
                           "/home/vadym/mexc-db-credentials.env"],
                          capture_output=True, text=True, check=True
                          ).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


def get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def real_interval(ex, sym):
    """Return (interval_hours, source_field, extra) — never guess."""
    try:
        if ex == "gate":
            d = get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{sym}")
            return d["funding_interval"] / 3600.0, "funding_interval(s)", d.get("quanto_multiplier")
        d = get(f"https://contract.mexc.com/api/v1/contract/funding_rate/{sym}")["data"]
        return float(d["collectCycle"]), "collectCycle(h)", None
    except Exception as e:
        print(f"[!] {ex}/{sym}: interval fetch FAILED {e!r}", file=sys.stderr)
        return None, "FETCH-FAILED", None


SQL = """
WITH ep AS (
  SELECT DISTINCT ON ((floor(extract(epoch FROM ts)/$3))::bigint)
         ts, funding_rate, (floor(extract(epoch FROM ts)/$3))::bigint e
  FROM funding_basis_snapshots
  WHERE exchange=$1 AND symbol=$2 AND funding_rate IS NOT NULL
  ORDER BY e, ts DESC)
SELECT count(*) n, avg(funding_rate) mean_r, stddev_pop(funding_rate) sd_r,
       min(funding_rate) mn, max(funding_rate) mx,
       100.0*count(*) FILTER (WHERE funding_rate>0)/count(*) pos
FROM ep
"""
SQL_SPR = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY perp_spread_bps) p,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY spot_spread_bps) s,
       avg(basis_bps) b
FROM funding_basis_snapshots
WHERE exchange=$1 AND symbol=$2 AND perp_bid>0 AND perp_ask>perp_bid
      AND spot_bid>0 AND spot_ask>spot_bid
"""


async def main():
    conn = await asyncpg.connect(dsn(), command_timeout=600)
    out = []
    for ex, sym in BASKET:
        ih, src, mult = real_interval(ex, sym)
        if ih is None:
            continue
        r8 = await conn.fetchrow(SQL, ex, sym, 28800)          # old 8h grid
        rt = await conn.fetchrow(SQL, ex, sym, int(ih * 3600))  # true grid
        sp = await conn.fetchrow(SQL_SPR, ex, sym)
        apr_old = float(r8["mean_r"]) * (24.0 / 8.0) * 365 * 100
        apr_new = float(rt["mean_r"]) * (24.0 / ih) * 365 * 100
        out.append({
            "ex": ex, "sym": sym, "ih": ih, "src": src, "mult": mult,
            "n8": r8["n"], "nt": rt["n"],
            "apr_old": apr_old, "apr_new": apr_new,
            "mean_r": float(rt["mean_r"]), "sd_r": float(rt["sd_r"]),
            "mn": float(rt["mn"]), "mx": float(rt["mx"]), "pos": float(rt["pos"]),
            "pspr": float(sp["p"]), "sspr": float(sp["s"]), "basis": float(sp["b"]),
        })
    await conn.close()

    print("=" * 124)
    print("A1 — REAL FUNDING INTERVAL vs the collector's hardcoded 8h")
    print("=" * 124)
    print(f"{'venue':<5} {'symbol':<12} {'real_iv':>8} {'source':<18} {'pays/day':>9} "
          f"{'APR_old(8h)':>12} {'APR_corrected':>14} {'factor':>7} {'epochs':>7} {'pos%':>6}")
    for r in out:
        f = r["apr_new"] / r["apr_old"] if r["apr_old"] else float("nan")
        print(f"{r['ex']:<5} {r['sym']:<12} {r['ih']:>7.1f}h {r['src']:<18} "
              f"{24/r['ih']:>9.1f} {r['apr_old']:>12.2f} {r['apr_new']:>14.2f} "
              f"{f:>6.2f}x {r['nt']:>7} {r['pos']:>6.1f}")

    def rtc(r, maker):
        return 4 * MAKER[r["ex"]] if maker else r["pspr"] + r["sspr"] + 4 * TAKER[r["ex"]]

    def net_notional(r, H, maker):
        return r["apr_new"] - rtc(r, maker) * (365.0 / H) / 100.0

    def net_cap(r, H, L, maker):
        return net_notional(r, H, maker) / (1.0 + 1.0 / L)

    print("\n" + "=" * 124)
    print("A2 — NET APR ON DEPLOYED CAPITAL   C = S + S/L   (interval-corrected gross)")
    print("     capital multiple: L=1x -> 2.00x notional | L=2x -> 1.50x | L=3x -> 1.33x")
    print("=" * 124)
    for maker in (True, False):
        tag = "MAKER entry/exit (realistic for carry — not queue-sensitive)" if maker \
              else "TAKER entry/exit (floor; also the cost of an emergency unwind)"
        print(f"\n--- {tag} ---")
        print(f"{'venue/symbol':<19} {'gross':>7} {'rt_bps':>7} | " +
              " | ".join(f"H={H}d L=1x   2x    3x" for H in HOLDS))
        for r in sorted(out, key=lambda r: -net_cap(r, 7, 2, True)):
            cells = []
            for H in HOLDS:
                cells.append(" ".join(f"{net_cap(r,H,L,maker):>6.1f}" for L in LEVS))
            print(f"{r['ex']+' '+r['sym']:<19} {r['apr_new']:>7.1f} {rtc(r,maker):>7.1f} | " +
                  " | ".join(cells))

    print("\n" + "=" * 124)
    print("A3 — RE-RANKED BASKET  (net-on-capital, L=2x, H=7d, MAKER) — raw components kept visible")
    print("=" * 124)
    print(f"{'#':<3} {'venue/symbol':<19} {'NET@L2,H7':>10} {'gross_corr':>11} {'gross_old':>10} "
          f"{'iv':>6} {'pspr':>6} {'sspr':>6} {'basis':>7} {'sd_fr%':>8} {'minFR%':>8} {'pos%':>6}")
    for i, r in enumerate(sorted(out, key=lambda r: -net_cap(r, 7, 2, True)), 1):
        print(f"{i:<3} {r['ex']+' '+r['sym']:<19} {net_cap(r,7,2,True):>10.2f} "
              f"{r['apr_new']:>11.2f} {r['apr_old']:>10.2f} {r['ih']:>5.0f}h "
              f"{r['pspr']:>6.2f} {r['sspr']:>6.2f} {r['basis']:>7.1f} "
              f"{r['sd_r']*100:>8.4f} {r['mn']*100:>8.4f} {r['pos']:>6.1f}")

    print("\n  NOTE: gross is on NOTIONAL; every NET column above is on DEPLOYED CAPITAL.")
    json.dump(out, open("partA.json", "w"), default=str)


asyncio.run(main())
