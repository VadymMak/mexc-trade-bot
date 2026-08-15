#!/usr/bin/env python3
"""
ЁРШ SENSITIVITY SWEEP — queue position x latency.

queue_frac = fraction of the resting size we must wait behind:
   1.0 = we join the BACK of the queue        (honest non-colocated retail)
   0.5 = we are mid-queue                     (optimistic)
   0.0 = we are FIRST in queue, ahead of all resting size
         (physically unachievable for us -- this is the colocated-HFT ideal)

If queue_frac=0.0 is still negative, ёрш is not a queue game we are losing:
it is unprofitable for ANYONE quoting this passively, and no amount of
colocation or rebate fixes it.
"""
import asyncio, bisect, statistics, subprocess, sys
from collections import defaultdict
import asyncpg

CANDIDATES = [("gate", "LA_USDT", 1e-4), ("gate", "ONE_USDT", 1e-6),
              ("gate", "MYX_USDT", 1e-4), ("gate", "BMT_USDT", 1e-5),
              ("mexc", "ONE_USDT", 1e-7)]
FEES = {"gate": -1.0, "mexc": 0.0}      # detector's optimistic assumption (gate rebate)
T_CYCLE = 60.0
QFRACS = [1.0, 0.5, 0.0]
LATS = [1000, 250, 50]


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
                               WHERE exchange=$1 AND symbol=$2 AND level=1 ORDER BY ts""", ex, sym)
    by = defaultdict(dict)
    for r in rows:
        by[r["ts"]][r["side"]] = (r["price"], r["size"])
    snaps = [(ts.timestamp(), d["bid"], d["ask"]) for ts, d in sorted(by.items())
             if "bid" in d and "ask" in d and d["ask"][0] > d["bid"][0] > 0]
    tape = await conn.fetch("""SELECT ts, price, size, side FROM tape_prints
                               WHERE exchange=$1 AND symbol=$2 AND price>0 AND size>0
                               ORDER BY ts""", ex, sym)
    return Book(snaps), [(r["ts"].timestamp(), r["price"], r["size"],
                          (r["side"] or "").lower()[:1]) for r in tape]


def run(ex, sym, tick, book, tape, lat_ms, qfrac):
    lat, tts = lat_ms / 1000.0, [p[0] for p in tape]
    fee = FEES[ex]
    tot = both_n = one_n = 0
    cap = adv = fees_paid = 0.0
    t, t_end, n = book.ts[0] + 1.0, book.ts[-1] - T_CYCLE, len(tape)
    while t < t_end:
        i = book.idx_at(t)
        if i is None:
            t += T_CYCLE
            continue
        b1, qb = book.bid[i]
        a1, qa = book.ask[i]
        qb, qa = qb * qfrac, qa * qfrac
        mid0 = (a1 + b1) / 2.0
        cb = ca = 0.0
        fb = fa = None
        k = bisect.bisect_left(tts, t + lat)
        dl = t + T_CYCLE
        while k < n and tape[k][0] <= dl:
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
        tot += 1
        if fb is not None and fa is not None:
            both_n += 1
            cap += (a1 - b1) / mid0 * 1e4
            fees_paid += 2 * fee
            t = max(fa, fb) + 0.5
        elif fb is not None or fa is not None:
            one_n += 1
            sgn = 1.0 if fb is not None else -1.0
            fpx = b1 if fb is not None else a1
            mend = book.mid_at(t + T_CYCLE)
            if mend:
                adv += sgn * (mend - fpx) / mid0 * 1e4
            fees_paid += fee
            t += T_CYCLE
        else:
            t += T_CYCLE
    net = (cap + adv - fees_paid) / tot if tot else float("nan")
    return {"tot": tot, "both": 100.0 * both_n / tot, "one": 100.0 * one_n / tot,
            "cap": cap / tot, "adv": adv / tot, "net": net}


async def main():
    conn = await asyncpg.connect(dsn())
    data = {}
    for ex, sym, tick in CANDIDATES:
        data[(ex, sym, tick)] = await load(conn, ex, sym)
    await conn.close()

    print("=" * 118)
    print("SENSITIVITY: NET bps per quoting cycle   (gate maker -1.0bp REBATE assumed, "
          "mexc maker 0.0bp — the most generous fees)")
    print("=" * 118)
    for lat in LATS:
        print(f"\n--- latency {lat} ms ---")
        print(f"{'venue/symbol':<20} " + "  ".join(
            f"{'qfrac=' + str(q):>13} {'both%':>6}" for q in QFRACS))
        for (ex, sym, tick), (book, tape) in data.items():
            cells = []
            for q in QFRACS:
                r = run(ex, sym, tick, book, tape, lat, q)
                cells.append(f"{r['net']:>13.2f} {r['both']:>5.1f}%")
            print(f"{ex+' '+sym:<20} " + "  ".join(cells))

    print("\n\n=== DETAIL AT THE COLOCATED IDEAL (qfrac=0.0, latency 50ms) ===")
    print("If these are negative, ёрш is not a queue race we lose — it is unprofitable outright.")
    print(f"{'venue/symbol':<20} {'cycles':>7} {'both%':>7} {'one%':>7} "
          f"{'spread_cap':>11} {'advsel':>9} {'NET':>9}")
    for (ex, sym, tick), (book, tape) in data.items():
        r = run(ex, sym, tick, book, tape, 50, 0.0)
        print(f"{ex+' '+sym:<20} {r['tot']:>7} {r['both']:>6.1f}% {r['one']:>6.1f}% "
              f"{r['cap']:>11.2f} {r['adv']:>9.2f} {r['net']:>9.2f}")


asyncio.run(main())
