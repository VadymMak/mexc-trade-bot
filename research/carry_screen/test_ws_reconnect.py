#!/usr/bin/env python3
"""Self-test for the zombie-socket fix in app/carry/depth_collectors.py.

Reproduces BOTH failure modes that run 1 could not survive and asserts the
collector now recovers from each, loudly.

  TEST A — SILENT STALL (the actual run-1 failure): subscribe to a contract
    that does not exist, so the socket connects fine and then never pushes
    anything. Under the old code this looped on `continue` forever. The
    watchdog must trip and force a reconnect.

  TEST B — HARD KILL: connect to real, actively-pushing symbols, wait for data,
    then abort the TCP transport underneath the client. The read loop must see
    the failure, log it with a reason, reconnect, and resume receiving.

Writes NOTHING to the database — the store is a stub. Read-only against the
venues (public depth channels).
"""
import asyncio
import logging
import os
import sys
import time
import types

os.environ["CARRY_STALE_SECS"] = "12"       # keep the watchdog test short

sys.path.insert(0, "/home/vadym/mexc-trade-bot/researcher")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("selftest")

from app.carry import depth_collectors as dc          # noqa: E402


class StubStore:
    """Counts snapshots instead of writing them."""

    def __init__(self):
        self.n = 0
        self.syms = set()

    async def add_snapshot(self, ex, sym, market, bids, asks, levels, ts=None):
        if bids and asks:
            self.n += 1
            self.syms.add(f"{ex}/{sym}")


# capture the live connection objects so TEST B can abort one
CAPTURED = []


class _Cap:
    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        ws = await self._inner.__aenter__()
        CAPTURED.append(ws)
        return ws

    async def __aexit__(self, *a):
        return await self._inner.__aexit__(*a)


_real_connect = dc.websockets.connect
# rebind only the name inside depth_collectors, not the real websockets module
dc.websockets = types.SimpleNamespace(connect=lambda *a, **k: _Cap(_real_connect(*a, **k)))


class LogTrap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    def matching(self, *needles):
        return [l for l in self.lines if all(n in l for n in needles)]


async def test_a_silent_stall():
    print("\n" + "=" * 78)
    print("TEST A — SILENT STALL (the run-1 failure): socket connects, venue never")
    print(f"         pushes. Watchdog is {dc._STALE_SECS:.0f}s. Expect a forced reconnect.")
    print("=" * 78)
    trap = LogTrap()
    logging.getLogger("app.carry.depth_collectors").addHandler(trap)
    store = StubStore()
    # a contract that does not exist -> subscribe is accepted/errored, no pushes
    col = dc.GatePerpDepth(store, ["ZZZZNOTREAL_USDT"], tag="stalltest")
    await col.start()
    await asyncio.sleep(dc._STALE_SECS + 14)
    await col.stop()
    logging.getLogger("app.carry.depth_collectors").removeHandler(trap)

    hits = trap.matching("DISCONNECTED", "watchdog")
    ok = col.reconnects >= 1 and hits
    print(f"  reconnects: {col.reconnects}   snapshots stored: {store.n} (expect 0)")
    for h in hits[:3]:
        print(f"  LOG: {h}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'} — watchdog "
          f"{'fired and forced a reconnect' if ok else 'did NOT fire'}")
    return bool(ok)


async def test_b_hard_kill():
    print("\n" + "=" * 78)
    print("TEST B — HARD KILL: abort the TCP transport under a live, pushing socket.")
    print("         Expect a logged disconnect, a reconnect, and data flowing again.")
    print("=" * 78)
    trap = LogTrap()
    logging.getLogger("app.carry.depth_collectors").addHandler(trap)
    store = StubStore()
    CAPTURED.clear()
    col = dc.MexcPerpDepth(store, ["BTC_USDT", "ETH_USDT"], tag="killtest")
    await col.start()

    # wait for the first real data
    t0 = time.monotonic()
    while store.n == 0 and time.monotonic() - t0 < 30:
        await asyncio.sleep(0.5)
    before = store.n
    print(f"  before kill: {before} snapshots, {len(CAPTURED)} connection(s)")
    if not before:
        print("  RESULT: INCONCLUSIVE — no data arrived to kill")
        await col.stop()
        return False

    CAPTURED[-1].transport.abort()           # rip the socket out from under it
    print("  transport.abort() called")

    t0 = time.monotonic()
    while col.reconnects == 0 and time.monotonic() - t0 < 40:
        await asyncio.sleep(0.5)
    after_reconnect = store.n
    t0 = time.monotonic()
    while store.n <= after_reconnect and time.monotonic() - t0 < 45:
        await asyncio.sleep(0.5)
    await col.stop()
    logging.getLogger("app.carry.depth_collectors").removeHandler(trap)

    resumed = store.n > after_reconnect
    hits = trap.matching("DISCONNECTED")
    ok = col.reconnects >= 1 and resumed
    print(f"  reconnects: {col.reconnects}   snapshots after recovery: {store.n}")
    for h in hits[:3]:
        print(f"  LOG: {h}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'} — "
          f"{'reconnected and resumed streaming' if ok else 'did not recover'}")
    return ok


async def main():
    a = await test_a_silent_stall()
    b = await test_b_hard_kill()
    print("\n" + "=" * 78)
    print(f"SELF-TEST: A(silent stall)={'PASS' if a else 'FAIL'}  "
          f"B(hard kill)={'PASS' if b else 'FAIL'}")
    print("=" * 78)
    return 0 if (a and b) else 1


sys.exit(asyncio.run(main()))
