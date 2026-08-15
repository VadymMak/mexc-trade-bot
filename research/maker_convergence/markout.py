#!/usr/bin/env python3
"""
HONEST MAKER-FILL MEASUREMENT (ёрш tick book + tape, 30 coin-venues, Aug 13-15).

This is the one thing in the archive that can actually settle the maker question,
because it has BOTH the resting book AND the tape that trades through it.

Model: we rest at the touch (best bid and/or best ask). We are filled ONLY when a
tape print actually trades through our level:
   resting BID  filled by a SELL-aggressor print at price <= our bid
   resting ASK  filled by a BUY-aggressor  print at price >= our ask
On fill we measure MARKOUT = (future mid - fill price), signed in our favour.

markout(t) IS the maker P&L of the fill: it already contains the half-spread we
earned, netted against the adverse price move that followed. If markout is
negative, posting at the touch loses money on average -> adverse selection beats
the spread we collect, and maker convergence cannot work on these books.
"""
import asyncio, sys, bisect, statistics, subprocess
from collections import defaultdict
import asyncpg

HORIZONS = [10, 60, 300]        # seconds
MAKER_FEE = {"mexc": 1.0, "gate": 2.0}


def dsn():
    return subprocess.run(
        ["sudo", "sed", "-n", "s/^DATABASE_URL=//p", "/home/vadym/mexc-db-credentials.env"],
        capture_output=True, text=True, check=True).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


async def main():
    conn = await asyncpg.connect(dsn())
    pairs = [(r["exchange"], r["symbol"]) for r in await conn.fetch(
        "SELECT exchange, symbol FROM book_ticker GROUP BY 1,2 ORDER BY 1,2")]

    rows_out = []
    for ex, sym in pairs:
        bk = await conn.fetch("""SELECT ts, best_bid, best_ask FROM book_ticker
                                 WHERE exchange=$1 AND symbol=$2 AND best_ask>best_bid AND best_bid>0
                                 ORDER BY ts""", ex, sym)
        tp = await conn.fetch("""SELECT ts, price, side FROM tape_prints
                                 WHERE exchange=$1 AND symbol=$2 AND price>0 ORDER BY ts""", ex, sym)
        if len(bk) < 500 or len(tp) < 500:
            continue
        bts = [r["ts"].timestamp() for r in bk]
        bbid = [r["best_bid"] for r in bk]
        bask = [r["best_ask"] for r in bk]
        bmid = [(a + b) / 2 for a, b in zip(bask, bbid)]

        def book_at(t):
            i = bisect.bisect_right(bts, t) - 1
            return i if i >= 0 else None

        def mid_at(t):
            i = book_at(t)
            return bmid[i] if i is not None else None

        fills = {h: [] for h in HORIZONS}
        hs_earned = []
        n_fill = 0
        t_end = bts[-1]
        for r in tp:
            t = r["ts"].timestamp()
            i = book_at(t)
            if i is None:
                continue
            side = (r["side"] or "").lower()
            px = r["price"]
            if side.startswith("s") and px <= bbid[i]:
                fill_px, sgn = bbid[i], +1.0        # we bought at our bid
            elif side.startswith("b") and px >= bask[i]:
                fill_px, sgn = bask[i], -1.0        # we sold at our ask
            else:
                continue
            m0 = bmid[i]
            if m0 <= 0 or fill_px <= 0:
                continue
            n_fill += 1
            hs_earned.append(sgn * (m0 - fill_px) / m0 * 1e4)
            for h in HORIZONS:
                if t + h > t_end:
                    continue
                mf = mid_at(t + h)
                if mf:
                    fills[h].append(sgn * (mf - fill_px) / m0 * 1e4)
        if n_fill < 100:
            continue
        row = {"ex": ex, "sym": sym, "n": n_fill,
               "hs": statistics.fmean(hs_earned) if hs_earned else float("nan"),
               "fee": MAKER_FEE[ex]}
        for h in HORIZONS:
            row[f"mo{h}"] = statistics.fmean(fills[h]) if fills[h] else float("nan")
            row[f"nm{h}"] = len(fills[h])
        rows_out.append(row)
        print(f"[i] {ex}/{sym} fills={n_fill}", file=sys.stderr)
    await conn.close()

    print("\n" + "=" * 108)
    print("HONEST MAKER MARKOUT — rest at touch, filled only when the tape trades through")
    print("hs = half-spread earned at fill; mo_Ns = markout after N seconds (already includes hs)")
    print("net = mo_60s - maker fee.  NEGATIVE net => posting at the touch loses money.")
    print("=" * 108)
    print(f"{'venue':<6} {'symbol':<15} {'fills':>7} {'hs_bps':>8} {'mo_10s':>8} {'mo_60s':>8} "
          f"{'mo_300s':>9} {'fee':>5} {'net_60s':>9}")
    for r in sorted(rows_out, key=lambda r: (r["ex"], r["sym"])):
        net = r["mo60"] - r["fee"]
        print(f"{r['ex']:<6} {r['sym']:<15} {r['n']:>7} {r['hs']:>8.2f} {r['mo10']:>8.2f} "
              f"{r['mo60']:>8.2f} {r['mo300']:>9.2f} {r['fee']:>5.1f} {net:>9.2f}")

    print("-" * 108)
    for ex in ["mexc", "gate"]:
        sub = [r for r in rows_out if r["ex"] == ex]
        if not sub:
            continue
        print(f"{ex.upper():<6} n={len(sub):<3} coins | median hs {statistics.median(r['hs'] for r in sub):>6.2f} "
              f"| median mo_10s {statistics.median(r['mo10'] for r in sub):>6.2f} "
              f"| mo_60s {statistics.median(r['mo60'] for r in sub):>6.2f} "
              f"| mo_300s {statistics.median(r['mo300'] for r in sub):>6.2f} "
              f"| median net_60s {statistics.median(r['mo60'] - r['fee'] for r in sub):>6.2f}")
    allr = rows_out
    pos = sum(1 for r in allr if r["mo60"] - r["fee"] > 0)
    print(f"\ncoin-venues with POSITIVE net maker markout @60s: {pos}/{len(allr)}")
    print(f"total fills measured: {sum(r['n'] for r in allr):,}")


asyncio.run(main())
