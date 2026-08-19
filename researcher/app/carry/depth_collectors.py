"""Depth collectors for the carry basket — perp via WS, spot via REST.

PERP (websocket):
  Gate : wss://fx-ws.gateio.ws/v4/ws/usdt
         {"channel":"futures.order_book","event":"subscribe",
          "payload":[contract, "50", "0"]}            -> full snapshot per change
  MEXC : wss://contract.mexc.com/edge
         {"method":"sub.depth.full","param":{"symbol":S,"limit":50}}
         MUST be sub.depth.full, NOT sub.depth — plain sub.depth sends unsorted
         incremental diffs and reading levels[0] as the touch yields nonsense
         (it made ONE_USDT look like a 444 bps market against a real 15 bps).

SPOT (REST polling):
  Gate : GET /api/v4/spot/order_book?currency_pair=X_USDT&limit=50
  MEXC : GET /api/v3/depth?symbol=XUSDT&limit=50

WHY REST FOR SPOT: MEXC's spot websocket is protobuf-framed and `protobuf` is not
installed in researcher/.venv. Installing it would mutate the shared venv every
other collector runs on, for no benefit here — capacity measurement needs depth
LEVELS, not tick-by-tick queue dynamics.

*** ZOMBIE-SOCKET FIX (2026-08-19) — read before touching the read loop ***
Run 1 collected 88 MINUTES of perp depth instead of 3.5 days. Both venues' perp
sockets went silent at 16:19:36 UTC, 3 seconds apart, and never reconnected,
while systemd cheerfully reported the unit `active (running)` for 3.5 days.
Three defects combined to make silence look like health:

  1. a recv timeout did `continue`, so a socket that died WITHOUT a FIN never
     raised, `_session()` never returned, and the reconnect path in `_run()`
     was never reached;
  2. the heartbeat `ws.send` sat inside a blanket `suppress(Exception)`, so
     send failures on that dead socket were swallowed too;
  3. nothing anywhere measured time-since-last-message, so there was no signal
     to act on and nothing in the log — the ABSENCE of errors was the symptom.

All three are fixed here:
  1. `_STALE_SECS` watchdog — no message on a connection for that long and the
     read loop RETURNS, which tears the socket down and forces a reconnect.
  2. the heartbeat is sent inline from the read loop and its exceptions
     PROPAGATE, so a failed ping reconnects instead of being suppressed.
  3. every connect, disconnect and reconnect logs with venue, chunk, symbol
     count and reason. Silence must never look like health again.

The read loop uses a short `_POLL_SECS` recv timeout purely as a scheduler tick
for the watchdog and the ping — it is NOT the staleness threshold.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import suppress

import aiohttp
import websockets

from .depth_store import CarryBookStore
from .depth_symbols import LEVELS, spot_symbol

logger = logging.getLogger(__name__)

_GATE_WS = "wss://fx-ws.gateio.ws/v4/ws/usdt"
_MEXC_WS = "wss://contract.mexc.com/edge"

_PING_INTERVAL = 20.0
_RECONNECT_DELAY = 5.0
# Scheduler tick for the read loop. Short so the watchdog and the ping are
# evaluated promptly; NOT a staleness threshold in itself.
_POLL_SECS = 5.0
# Watchdog: no message at all on a connection for this long -> reconnect.
# Env-overridable so the reconnect path can be exercised in a self-test without
# waiting a minute. Every perp stream pushes far more often than this in
# normal operation, so a trip means the socket really is dead.
_STALE_SECS = float(os.getenv("CARRY_STALE_SECS", "45"))

# One full sweep of the spot universe per _SPOT_POLL_SECS. Polling faster than
# the store's snapshot throttle only produces work the throttle discards.
_SPOT_POLL_SECS = float(os.getenv("CARRY_SPOT_POLL_SECS", "45"))
_SPOT_CONCURRENCY = 8


def _levels(raw) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for lv in raw or []:
        try:
            if isinstance(lv, dict):
                out.append((float(lv.get("p", lv.get("price"))),
                            float(lv.get("s", lv.get("v", lv.get("size"))))))
            elif isinstance(lv, (list, tuple)) and len(lv) >= 2:
                out.append((float(lv[0]), float(lv[1])))
        except (TypeError, ValueError, KeyError):
            continue
    return out


class _WsDepth:
    """One websocket connection carrying one chunk of one venue's symbols.

    Subclasses supply the URL, the subscribe frames, the ping frame and a
    message handler. Everything about staying alive lives here.
    """

    venue = "?"
    url = ""

    def __init__(self, store: CarryBookStore, symbols: list[str], tag: str = "") -> None:
        self._store = store
        self._symbols = [s.upper() for s in symbols]
        self._tag = tag or "0"
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.reconnects = 0
        self.msgs = 0
        self.last_msg_at = 0.0

    @property
    def name(self) -> str:
        return f"{self.venue}#{self._tag}"

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"ws-{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    # -- to implement -------------------------------------------------------
    async def _subscribe(self, ws) -> None:
        raise NotImplementedError

    async def _ping(self, ws) -> None:
        raise NotImplementedError

    async def _handle(self, msg: dict) -> None:
        raise NotImplementedError

    # -- supervision --------------------------------------------------------
    async def _run(self) -> None:
        """Reconnect forever. EVERY exit from _session() is logged with a
        reason — this loop is the thing that was unreachable in run 1."""
        while not self._stop.is_set():
            try:
                reason = await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = f"exception {exc!r}"
            if self._stop.is_set():
                return
            self.reconnects += 1
            logger.warning("[carry/l2/%s] DISCONNECTED (%s) — reconnect #%d in %.0fs "
                           "(%d symbols)", self.name, reason, self.reconnects,
                           _RECONNECT_DELAY, len(self._symbols))
            await asyncio.sleep(_RECONNECT_DELAY)

    async def _session(self) -> str:
        """Hold one connection. Returns the reason it ended; never returns
        while the socket is healthy."""
        async with websockets.connect(self.url, max_size=4_194_304) as ws:
            await self._subscribe(ws)
            logger.info("[carry/l2/%s] connected, subscribed %d symbols "
                        "(levels=%d, stale watchdog %.0fs)",
                        self.name, len(self._symbols), LEVELS, _STALE_SECS)
            now = time.monotonic()
            last_msg = last_ping = now
            self.last_msg_at = now
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=_POLL_SECS)
                except asyncio.TimeoutError:
                    raw = None
                except asyncio.CancelledError:
                    raise
                now = time.monotonic()

                if raw is None:
                    # WATCHDOG. This is the whole fix: silence is a failure,
                    # not a reason to loop again.
                    if now - last_msg > _STALE_SECS:
                        return f"no data for {now - last_msg:.0f}s (watchdog)"
                else:
                    last_msg = now
                    self.last_msg_at = now
                    self.msgs += 1

                # Heartbeat on a schedule REGARDLESS of traffic. MEXC closes an
                # unpinged socket (1005) after ~70s even while it is actively
                # pushing to us, so a ping that only fires in idle gaps never
                # fires at all on a busy stream. NOT suppressed: a failed ping
                # must propagate and reconnect.
                if now - last_ping >= _PING_INTERVAL:
                    await self._ping(ws)
                    last_ping = now

                if raw is None:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await self._handle(msg)
            return "stopping"


class GatePerpDepth(_WsDepth):
    venue = "gate-perp"
    url = _GATE_WS

    async def _subscribe(self, ws) -> None:
        for c in self._symbols:
            await ws.send(json.dumps({
                "time": int(time.time()), "channel": "futures.order_book",
                "event": "subscribe", "payload": [c, str(LEVELS), "0"],
            }))

    async def _ping(self, ws) -> None:
        await ws.send(json.dumps({"time": int(time.time()),
                                  "channel": "futures.ping"}))

    async def _handle(self, msg: dict) -> None:
        if msg.get("event") == "subscribe":
            if msg.get("error"):
                logger.warning("[carry/l2/%s] subscribe error: %s",
                               self.name, msg["error"])
            return
        if msg.get("channel") != "futures.order_book":
            return
        res = msg.get("result")
        if not res:
            return
        sym = res.get("contract") or res.get("s")
        if not sym:
            return
        await self._store.add_snapshot(
            "gate", sym, "perp",
            _levels(res.get("bids")), _levels(res.get("asks")), LEVELS)


class MexcPerpDepth(_WsDepth):
    venue = "mexc-perp"
    url = _MEXC_WS

    async def _subscribe(self, ws) -> None:
        for s in self._symbols:
            await ws.send(json.dumps({"method": "sub.depth.full",
                                      "param": {"symbol": s, "limit": LEVELS}}))

    async def _ping(self, ws) -> None:
        await ws.send(json.dumps({"method": "ping"}))

    async def _handle(self, msg: dict) -> None:
        if msg.get("channel") != "push.depth.full":
            return
        data = msg.get("data") or {}
        sym = msg.get("symbol") or data.get("symbol")
        if not sym:
            return
        await self._store.add_snapshot(
            "mexc", sym, "perp",
            _levels(data.get("bids")), _levels(data.get("asks")), LEVELS)


class SpotDepthPoller:
    """REST poller for spot books on both venues.

    REST needs no watchdog — a failed poll is a caught exception and the next
    sweep retries — but it does need to be VISIBLE, so consecutive failures per
    symbol are counted and logged rather than silently returning.
    """

    def __init__(self, store: CarryBookStore, basket: list[tuple[str, str]]) -> None:
        self._store = store
        self._basket = basket
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._fails: dict[tuple[str, str], int] = {}
        self.polls = 0
        self.errors = 0

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="spot-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=10)
        sem = asyncio.Semaphore(_SPOT_CONCURRENCY)

        async def guarded(sess, ex, sym):
            async with sem:
                await self._one(sess, ex, sym)

        async with aiohttp.ClientSession(timeout=timeout) as sess:
            while not self._stop.is_set():
                t0 = time.monotonic()
                await asyncio.gather(*(guarded(sess, ex, sym)
                                       for ex, sym in self._basket),
                                     return_exceptions=True)
                took = time.monotonic() - t0
                if took > _SPOT_POLL_SECS:
                    logger.warning("[carry/l2/spot] sweep took %.1fs > poll "
                                   "interval %.0fs — falling behind", took,
                                   _SPOT_POLL_SECS)
                await asyncio.sleep(max(0.0, _SPOT_POLL_SECS - took))

    def _note(self, ex: str, sym: str, ok: bool, why: str = "") -> None:
        key = (ex, sym)
        if ok:
            if self._fails.pop(key, 0) >= 5:
                logger.info("[carry/l2/spot] %s/%s recovered", ex, sym)
            return
        self.errors += 1
        n = self._fails[key] = self._fails.get(key, 0) + 1
        # log the 5th consecutive failure and every 50th after, so a
        # permanently broken symbol is loud once and not every sweep
        if n == 5 or (n > 5 and n % 50 == 0):
            logger.warning("[carry/l2/spot] %s/%s failing %d sweeps in a row: %s",
                           ex, sym, n, why)

    async def _one(self, sess, ex: str, sym: str) -> None:
        native = spot_symbol(ex, sym)
        if ex == "gate":
            url = ("https://api.gateio.ws/api/v4/spot/order_book"
                   f"?currency_pair={native}&limit={LEVELS}")
        else:
            url = f"https://api.mexc.com/api/v3/depth?symbol={native}&limit={LEVELS}"
        try:
            async with sess.get(url) as r:
                if r.status != 200:
                    self._note(ex, sym, False, f"HTTP {r.status}")
                    return
                d = await r.json()
        except Exception as exc:
            self._note(ex, sym, False, repr(exc))
            return
        self.polls += 1
        self._note(ex, sym, True)
        await self._store.add_snapshot(ex, sym, "spot",
                                       _levels(d.get("bids")),
                                       _levels(d.get("asks")), LEVELS)
