#!/usr/bin/env python3
"""
ЁРШ QUEUE-AWARE MAKER-FILL SIMULATOR  (read-only)

Question: can a NON-COLOCATED bot actually farm the 1-tick spread on the five
locked-1-tick candidates, once it must (a) sit BEHIND the resting queue and
(b) react with real latency?

MODEL (all choices stated explicitly, all deliberately on the pessimistic side
except where marked OPTIMISTIC):

 * Units: queue arithmetic is done in CONTRACTS. Both ersh_book_l2.size and
   tape_prints.size are contracts on both venues, so no multiplier is involved
   and there is no unit risk.

 * Posting: we post at the TOUCH (best bid to buy / best ask to sell) and we
   join the BACK of the queue -> queue_ahead = full resting size at that level
   from the last L2 snapshot we could have seen (>= latency X before now).

 * Fill: we fill only after the queue ahead of us is consumed by tape that
   trades AT or THROUGH our level, in the correct aggressor direction:
       our BUY  at Pb  <- consumed by SELL-aggressor prints with price <= Pb
       our SELL at Pa  <- consumed by BUY-aggressor  prints with price >= Pa
   Binary fill once cumulative consumption exceeds queue_ahead.
   OPTIMISTIC: our own order size is treated as infinitesimal (0 contracts),
   so we never have to wait for our own size to clear.

 * Re-quote: if the touch moves AWAY from our price (market ran up while we bid),
   our order is stale -> we CANCEL and re-post at the new touch after latency X,
   which RESETS queue_ahead to the full new resting size. This is the honest
   choice: a non-colocated bot cannot keep an old queue position at a new price.
   If instead the level EMPTIES beneath us (best bid falls below our price), we
   become the touch with queue_ahead = 0 and keep resting -- the adverse case.

 * Latency X: we cannot observe or act on anything newer than X ms.
   CAVEAT: L2 snapshots arrive every ~1.2-3.3 s, which is far coarser than X,
   so this sim CANNOT resolve sub-second latency. X mainly selects which
   snapshot we read queue_ahead from. Latency here is bounded, not measured.

 * Round trip: passive entry fill, then post passive exit on the OTHER side at
   the then-current touch, same queue rules. If the exit does not fill within
   T_INV, we hold unoffloaded inventory and mark it to mid (reported separately
   as inventory risk, NOT counted as profit).

 * Markout is always measured from BOOK MID at fill time, never trade price.
"""
import asyncio, bisect, statistics, subprocess, sys, json
from collections import defaultdict
import asyncpg

CANDIDATES = [
    ("gate", "LA_USDT",  1e-4),
    ("gate", "ONE_USDT", 1e-6),
    ("gate", "MYX_USDT", 1e-4),
    ("gate", "BMT_USDT", 1e-5),
    ("mexc", "ONE_USDT", 1e-7),
]
# maker fee in bps, per side. MEXC futures maker = 0% (backend/app/market_data/mexc_http.py:230).
# Gate is run under two scenarios because the -1.0 bp rebate in l2_symbols.py is UNVERIFIED.
FEE_SCENARIOS = {"gate_rebate_-1.0bp": {"gate": -1.0, "mexc": 0.0},
                 "gate_retail_+2.0bp": {"gate": 2.0, "mexc": 0.0}}

LATENCY_MS = 250
HORIZONS = [1, 5, 30, 60]
T_ENTRY = 120.0     # abandon an unfilled entry after this long
T_INV = 300.0       # exit leg must fill within this or it is unoffloaded inventory


def dsn():
    return subprocess.run(["sudo", "sed", "-n", "s/^DATABASE_URL=//p",
                           "/home/vadym/mexc-db-credentials.env"],
                          capture_output=True, text=True, check=True
                          ).stdout.strip().replace("postgresql+asyncpg://", "postgresql://")


class Book:
    """Top-of-book time series rebuilt from ersh_book_l2 (contracts)."""
    def __init__(self, snaps):
        self.ts = [s[0] for s in snaps]
        self.bid = [s[1] for s in snaps]      # (price, size_contracts)
        self.ask = [s[2] for s in snaps]

    def idx_at(self, t):
        i = bisect.bisect_right(self.ts, t) - 1
        return i if i >= 0 else None

    def mid_at(self, t):
        i = self.idx_at(t)
        if i is None:
            return None
        return (self.bid[i][0] + self.ask[i][0]) / 2.0


async def load(conn, ex, sym):
    rows = await conn.fetch("""
        SELECT ts, side, level, price, size FROM ersh_book_l2
        WHERE exchange=$1 AND symbol=$2 AND level=1 ORDER BY ts, side""", ex, sym)
    by = defaultdict(dict)
    for r in rows:
        by[r["ts"]][r["side"]] = (r["price"], r["size"])
    snaps = []
    for ts in sorted(by):
        d = by[ts]
        if "bid" in d and "ask" in d and d["ask"][0] > d["bid"][0] > 0:
            snaps.append((ts.timestamp(), d["bid"], d["ask"]))
    tape = await conn.fetch("""
        SELECT ts, price, size, side FROM tape_prints
        WHERE exchange=$1 AND symbol=$2 AND price>0 AND size>0 ORDER BY ts""", ex, sym)
    tp = [(r["ts"].timestamp(), r["price"], r["size"], (r["side"] or "").lower()[:1])
          for r in tape]
    return Book(snaps), tp


def rest_and_fill(book, tape, t_start, want, lat, t_max):
    """Rest passively at the touch until the queue ahead is consumed.

    want='b' -> we are buying at the best bid; want='a' -> selling at the best ask.
    Returns (fill_price, fill_ts, n_requotes, spread_ticks_at_post) or None.
    """
    t = t_start + lat
    i = book.idx_at(t)
    if i is None:
        return None
    px = book.bid[i][0] if want == "b" else book.ask[i][0]
    qahead = book.bid[i][1] if want == "b" else book.ask[i][1]
    spread0 = book.ask[i][0] - book.bid[i][0]
    consumed = 0.0
    k = bisect.bisect_left([p[0] for p in tape], t) if False else None
    # tape pointer via bisect on a cached key list
    k = bisect.bisect_left(TAPE_TS, t)
    requotes = 0
    deadline = t_start + t_max
    n = len(tape)
    while k < n and tape[k][0] <= deadline:
        tts, tpx, tsz, tside = tape[k]
        # advance the book to this print and check whether our quote is stale
        j = book.idx_at(tts - lat)
        if j is not None:
            best = book.bid[j][0] if want == "b" else book.ask[j][0]
            moved_away = (best > px) if want == "b" else (best < px)
            if moved_away:
                # touch ran away from us -> cancel, re-post at new touch, queue resets
                requotes += 1
                px = best
                qahead = book.bid[j][1] if want == "b" else book.ask[j][1]
                consumed = 0.0
            else:
                emptied = (best < px) if want == "b" else (best > px)
                if emptied:
                    qahead = 0.0     # level emptied beneath us; we are now the touch
        hits = (tside == "s" and tpx <= px) if want == "b" else (tside == "b" and tpx >= px)
        if hits:
            consumed += tsz
            if consumed > qahead:
                return px, tts, requotes, spread0
        k += 1
    return None


def simulate(ex, sym, tick, book, tape, lat_ms):
    global TAPE_TS
    TAPE_TS = [p[0] for p in tape]
    lat = lat_ms / 1000.0
    trips, t_end = [], book.ts[-1]
    for first in ("b", "a"):
        t = book.ts[0] + 1.0
        while t < t_end - T_INV:
            ent = rest_and_fill(book, tape, t, first, lat, T_ENTRY)
            if ent is None:
                t += T_ENTRY
                continue
            epx, ets, ereq, spread0 = ent
            mid_e = book.mid_at(ets)
            other = "a" if first == "b" else "b"
            ex_ = rest_and_fill(book, tape, ets, other, lat, T_INV)
            sgn = 1.0 if first == "b" else -1.0
            mo = {}
            for h in HORIZONS:
                mf = book.mid_at(ets + h)
                mo[h] = (sgn * (mf - epx) / mid_e * 1e4) if (mf and mid_e) else None
            rec = {"ex": ex, "sym": sym, "entry_ts": ets, "entry_px": epx,
                   "mid_e": mid_e, "requotes": ereq,
                   "spread_ticks": max(1, round(spread0 / tick)),
                   "markout": mo, "side": first}
            if ex_ is None:
                rec["filled_exit"] = False
                mf = book.mid_at(ets + T_INV)
                rec["gross_bps"] = (sgn * (mf - epx) / mid_e * 1e4) if (mf and mid_e) else None
                t = ets + T_INV
            else:
                xpx, xts, xreq, _ = ex_
                rec["filled_exit"] = True
                rec["exit_ts"] = xts
                rec["gross_bps"] = sgn * (xpx - epx) / mid_e * 1e4
                rec["hold_s"] = xts - ets
                rec["requotes"] += xreq
                t = xts + 0.5
            trips.append(rec)
    return trips


def report(all_trips):
    print("\n" + "=" * 122)
    print(f"QUEUE-AWARE ROUND TRIPS  (latency {LATENCY_MS} ms, entry timeout {T_ENTRY:.0f}s, "
          f"inventory timeout {T_INV:.0f}s)")
    print("=" * 122)
    for scen, fees in FEE_SCENARIOS.items():
        print(f"\n--- fee scenario: {scen}  (mexc maker 0.0 bp, gate maker {fees['gate']:+.1f} bp) ---")
        print(f"{'venue/symbol':<20} {'regime':<10} {'trips':>6} {'exit_fill%':>10} "
              f"{'gross':>8} {'net':>8} {'win%':>6} {'mo@60s':>8} {'med_hold':>9}")
        rows = []
        for (ex, sym), trips in sorted(all_trips.items()):
            for regime, want in (("locked-1tk", lambda r: r["spread_ticks"] <= 1),
                                 ("widened", lambda r: r["spread_ticks"] > 1)):
                sub = [r for r in trips if want(r) and r["gross_bps"] is not None]
                if len(sub) < 5:
                    continue
                fee = fees[ex] * 2.0     # both legs are passive
                nets = [r["gross_bps"] - fee for r in sub]
                filled = [r for r in sub if r["filled_exit"]]
                mo60 = [r["markout"][60] for r in sub if r["markout"][60] is not None]
                holds = [r["hold_s"] for r in filled if "hold_s" in r]
                rows.append((ex + " " + sym, regime, len(sub),
                             100.0 * len(filled) / len(sub),
                             statistics.fmean(r["gross_bps"] for r in sub),
                             statistics.fmean(nets),
                             100.0 * sum(1 for v in nets if v > 0) / len(nets),
                             statistics.fmean(mo60) if mo60 else float("nan"),
                             statistics.median(holds) if holds else float("nan")))
        for r in sorted(rows, key=lambda r: -r[5]):
            print(f"{r[0]:<20} {r[1]:<10} {r[2]:>6} {r[3]:>9.1f}% {r[4]:>8.2f} {r[5]:>8.2f} "
                  f"{r[6]:>5.1f}% {r[7]:>8.2f} {r[8]:>8.1f}s")


async def main():
    conn = await asyncpg.connect(dsn())
    all_trips = {}
    for ex, sym, tick in CANDIDATES:
        book, tape = await load(conn, ex, sym)
        print(f"[i] {ex}/{sym}: {len(book.ts)} L2 snaps, {len(tape)} prints", file=sys.stderr)
        trips = simulate(ex, sym, tick, book, tape, LATENCY_MS)
        all_trips[(ex, sym)] = trips
        print(f"[i]    -> {len(trips)} round trips", file=sys.stderr)
    await conn.close()

    print("\n=== ENTRY-SIDE DIAGNOSTICS ===")
    print(f"{'venue/symbol':<20} {'trips':>7} {'exit_fill%':>11} {'med_requotes':>13} "
          f"{'mo@1s':>7} {'mo@5s':>7} {'mo@30s':>8} {'mo@60s':>8}")
    for (ex, sym), trips in sorted(all_trips.items()):
        if not trips:
            print(f"{ex+' '+sym:<20} {'0':>7}  (no fills)")
            continue
        f = [r for r in trips if r["filled_exit"]]
        cells = []
        for h in HORIZONS:
            v = [r["markout"][h] for r in trips if r["markout"][h] is not None]
            cells.append(statistics.fmean(v) if v else float("nan"))
        print(f"{ex+' '+sym:<20} {len(trips):>7} {100.0*len(f)/len(trips):>10.1f}% "
              f"{statistics.median(r['requotes'] for r in trips):>13.0f} "
              f"{cells[0]:>7.2f} {cells[1]:>7.2f} {cells[2]:>8.2f} {cells[3]:>8.2f}")

    report(all_trips)

    print("\n=== UNOFFLOADED INVENTORY (entry filled, exit never filled within "
          f"{T_INV:.0f}s) ===")
    for (ex, sym), trips in sorted(all_trips.items()):
        if not trips:
            continue
        stuck = [r for r in trips if not r["filled_exit"]]
        if not stuck:
            print(f"{ex+' '+sym:<20} none")
            continue
        v = [r["gross_bps"] for r in stuck if r["gross_bps"] is not None]
        print(f"{ex+' '+sym:<20} {len(stuck):>5}/{len(trips)} trips "
              f"({100.0*len(stuck)/len(trips):>5.1f}%), mark-to-mid "
              f"{statistics.fmean(v) if v else float('nan'):>7.2f} bps")

    with open("sim_out.json", "w") as fh:
        json.dump({f"{k[0]}/{k[1]}": len(v) for k, v in all_trips.items()}, fh)


TAPE_TS = []
asyncio.run(main())
