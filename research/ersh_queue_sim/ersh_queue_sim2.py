#!/usr/bin/env python3
"""
ЁРШ QUEUE-AWARE MAKER SIMULATOR v2 — TWO-SIDED QUOTING (read-only)

v1 was wrong: it filled one leg then CHASED the touch with the other, which is
momentum-chasing, not spread capture, and lost by construction. This version
models what ёрш actually is:

  post a bid at b1 AND an ask at a1 simultaneously, join the BACK of both
  queues, do not chase. You capture the spread when BOTH sides fill (the price
  oscillated). You get hurt when only ONE side fills and the price keeps going
  (the price trended) -- that is adverse selection, priced honestly.

This is why the detector ranked on rev% (reversal rate): LA at 97.8 % is a price
that does nothing but bounce, which is exactly the two-sided-fill case.

MODEL / ASSUMPTIONS
 * Units: CONTRACTS throughout. ersh_book_l2.size and tape_prints.size are both
   contracts on both venues -> no multiplier, no unit risk.
 * queue_ahead = full resting size at our level from the last L2 snapshot
   observable at least `latency` before the print. We are always LAST in queue.
 * Fill: our BUY at b1 needs SELL-aggressor prints with price <= b1 to consume
   queue_bid; our SELL at a1 needs BUY-aggressor prints with price >= a1.
   OPTIMISTIC: our own size is treated as 0 contracts.
 * No chasing, no re-quote inside a cycle. A cycle lasts T_CYCLE, then we cancel
   whatever is unfilled and mark any inventory to BOOK MID.
 * Markout always from BOOK MID, never trade price.
 * Latency X gates which snapshot we may read. CAVEAT: L2 snapshots land every
   ~1.2-3.3 s, far coarser than X, so sub-second latency is NOT resolvable here.
"""
import asyncio, bisect, statistics, subprocess, sys
from collections import defaultdict
import asyncpg

CANDIDATES = [("gate", "LA_USDT", 1e-4), ("gate", "ONE_USDT", 1e-6),
              ("gate", "MYX_USDT", 1e-4), ("gate", "BMT_USDT", 1e-5),
              ("mexc", "ONE_USDT", 1e-7)]
FEE_SCENARIOS = {"gate_rebate_-1.0bp": {"gate": -1.0, "mexc": 0.0},
                 "gate_retail_+2.0bp": {"gate": 2.0, "mexc": 0.0}}
LATENCY_MS = 250
T_CYCLE = 60.0          # quote lifetime; unfilled side cancelled, inventory marked to mid
HORIZONS = [1, 5, 30, 60]


def dsn():
    return subprocess.run(["sudo", "sed", "-n", "s/^DATABASE_URL=//p",
                           "/home/vadym/mexc-db-credentials.env"],
                          capture_output=True, text=True, check=True
                          ).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


class Book:
    def __init__(self, snaps):
        self.ts = [s[0] for s in snaps]
        self.bid = [s[1] for s in snaps]
        self.ask = [s[2] for s in snaps]

    def idx_at(self, t):
        i = bisect.bisect_right(self.ts, t) - 1
        return i if i >= 0 else None

    def mid_at(self, t):
        i = self.idx_at(t)
        return (self.bid[i][0] + self.ask[i][0]) / 2.0 if i is not None else None


async def load(conn, ex, sym):
    rows = await conn.fetch("""SELECT ts, side, price, size FROM ersh_book_l2
                               WHERE exchange=$1 AND symbol=$2 AND level=1
                               ORDER BY ts""", ex, sym)
    by = defaultdict(dict)
    for r in rows:
        by[r["ts"]][r["side"]] = (r["price"], r["size"])
    snaps = [(ts.timestamp(), d["bid"], d["ask"]) for ts, d in sorted(by.items())
             if "bid" in d and "ask" in d and d["ask"][0] > d["bid"][0] > 0]
    tape = await conn.fetch("""SELECT ts, price, size, side FROM tape_prints
                               WHERE exchange=$1 AND symbol=$2 AND price>0 AND size>0
                               ORDER BY ts""", ex, sym)
    tp = [(r["ts"].timestamp(), r["price"], r["size"], (r["side"] or "").lower()[:1])
          for r in tape]
    return Book(snaps), tp


def simulate(ex, sym, tick, book, tape, lat_ms):
    lat = lat_ms / 1000.0
    tts_list = [p[0] for p in tape]
    cycles = []
    t = book.ts[0] + 1.0
    t_end = book.ts[-1] - T_CYCLE
    while t < t_end:
        i = book.idx_at(t)
        if i is None:
            t += T_CYCLE
            continue
        b1, qb = book.bid[i]
        a1, qa = book.ask[i]
        mid0 = (a1 + b1) / 2.0
        spread_ticks = max(1, round((a1 - b1) / tick))
        cb = ca = 0.0
        fb = fa = None          # fill timestamps
        k = bisect.bisect_left(tts_list, t + lat)
        deadline = t + T_CYCLE
        n = len(tape)
        while k < n and tape[k][0] <= deadline:
            tsx, tpx, tsz, tsd = tape[k]
            if fb is None and tsd == "s" and tpx <= b1:
                cb += tsz
                if cb > qb:
                    fb = tsx
            if fa is None and tsd == "b" and tpx >= a1:
                ca += tsz
                if ca > qa:
                    fa = tsx
            if fb is not None and fa is not None:
                break
            k += 1
        rec = {"t": t, "spread_ticks": spread_ticks, "mid0": mid0,
               "spread_bps": (a1 - b1) / mid0 * 1e4,
               "fb": fb, "fa": fa, "qb": qb, "qa": qa}
        if fb is not None and fa is not None:
            rec["kind"] = "both"
            rec["gross_bps"] = (a1 - b1) / mid0 * 1e4
            rec["hold"] = abs(fa - fb)
            t = max(fa, fb) + 0.5
        elif fb is not None or fa is not None:
            rec["kind"] = "one"
            side = "b" if fb is not None else "a"
            fpx = b1 if side == "b" else a1
            ft = fb if fb is not None else fa
            sgn = 1.0 if side == "b" else -1.0
            mend = book.mid_at(t + T_CYCLE)
            rec["gross_bps"] = (sgn * (mend - fpx) / mid0 * 1e4) if mend else None
            rec["side"] = side
            rec["mo"] = {}
            for h in HORIZONS:
                mf = book.mid_at(ft + h)
                rec["mo"][h] = (sgn * (mf - fpx) / mid0 * 1e4) if mf else None
            t = t + T_CYCLE
        else:
            rec["kind"] = "none"
            rec["gross_bps"] = 0.0
            t = t + T_CYCLE
        cycles.append(rec)
    return cycles


def main_report(all_cycles):
    print("\n" + "=" * 128)
    print(f"TWO-SIDED QUOTING, QUEUE-AWARE   latency={LATENCY_MS}ms   cycle={T_CYCLE:.0f}s   "
          "(we are always LAST in queue, our own size = 0)")
    print("=" * 128)

    print("\n=== CYCLE OUTCOMES (what actually happens when you quote both sides) ===")
    print(f"{'venue/symbol':<20} {'cycles':>7} {'both%':>7} {'one-sided%':>11} {'none%':>7} "
          f"{'med_spread':>11} {'med_qb':>10} {'med_qa':>10}")
    for (ex, sym), cy in sorted(all_cycles.items()):
        if not cy:
            continue
        nb = sum(1 for c in cy if c["kind"] == "both")
        no = sum(1 for c in cy if c["kind"] == "one")
        nn = sum(1 for c in cy if c["kind"] == "none")
        print(f"{ex+' '+sym:<20} {len(cy):>7} {100*nb/len(cy):>6.1f}% {100*no/len(cy):>10.1f}% "
              f"{100*nn/len(cy):>6.1f}% {statistics.median(c['spread_bps'] for c in cy):>10.2f}b "
              f"{statistics.median(c['qb'] for c in cy):>10.0f} "
              f"{statistics.median(c['qa'] for c in cy):>10.0f}")

    print("\n=== ADVERSE SELECTION ON ONE-SIDED FILLS (markout from book mid, bps) ===")
    print(f"{'venue/symbol':<20} {'n':>7} {'mo@1s':>8} {'mo@5s':>8} {'mo@30s':>8} {'mo@60s':>8}")
    for (ex, sym), cy in sorted(all_cycles.items()):
        one = [c for c in cy if c["kind"] == "one"]
        if len(one) < 5:
            continue
        cells = []
        for h in HORIZONS:
            v = [c["mo"][h] for c in one if c["mo"].get(h) is not None]
            cells.append(statistics.fmean(v) if v else float("nan"))
        print(f"{ex+' '+sym:<20} {len(one):>7} {cells[0]:>8.2f} {cells[1]:>8.2f} "
              f"{cells[2]:>8.2f} {cells[3]:>8.2f}")

    for scen, fees in FEE_SCENARIOS.items():
        print(f"\n=== NET P&L PER CYCLE — fee scenario {scen} ===")
        print(f"{'venue/symbol':<20} {'regime':<11} {'cycles':>7} {'both%':>7} "
              f"{'spread_cap':>11} {'advsel':>9} {'fees':>7} {'NET/cycle':>10}")
        rows = []
        for (ex, sym), cy in sorted(all_cycles.items()):
            for regime, want in (("locked-1tk", lambda c: c["spread_ticks"] <= 1),
                                 ("widened", lambda c: c["spread_ticks"] > 1)):
                sub = [c for c in cy if want(c)]
                if len(sub) < 20:
                    continue
                both = [c for c in sub if c["kind"] == "both"]
                one = [c for c in sub if c["kind"] == "one" and c["gross_bps"] is not None]
                fee = fees[ex]
                # spread captured on both-filled cycles, minus 2 maker fees
                cap = sum(c["gross_bps"] - 2 * fee for c in both)
                # adverse selection on one-sided fills, minus 1 maker fee
                adv = sum(c["gross_bps"] - fee for c in one)
                net = (cap + adv) / len(sub)
                rows.append((f"{ex} {sym}", regime, len(sub),
                             100.0 * len(both) / len(sub),
                             sum(c["gross_bps"] for c in both) / len(sub),
                             sum(c["gross_bps"] for c in one) / len(sub),
                             (2 * fee * len(both) + fee * len(one)) / len(sub),
                             net))
        for r in sorted(rows, key=lambda r: -r[7]):
            print(f"{r[0]:<20} {r[1]:<11} {r[2]:>7} {r[3]:>6.1f}% {r[4]:>11.2f} "
                  f"{r[5]:>9.2f} {r[6]:>7.2f} {r[7]:>10.2f}")
        print("  spread_cap = spread earned on both-filled cycles, averaged over ALL cycles")
        print("  advsel     = P&L of one-sided (adversely selected) fills, averaged over ALL cycles")
        print("  NET/cycle  = spread_cap + advsel - fees   (bps of notional per quoting cycle)")


async def main():
    conn = await asyncpg.connect(dsn())
    out = {}
    for ex, sym, tick in CANDIDATES:
        book, tape = await load(conn, ex, sym)
        cy = simulate(ex, sym, tick, book, tape, LATENCY_MS)
        out[(ex, sym)] = cy
        print(f"[i] {ex}/{sym}: {len(book.ts)} snaps, {len(tape)} prints -> {len(cy)} cycles",
              file=sys.stderr)
    await conn.close()
    main_report(out)


asyncio.run(main())
