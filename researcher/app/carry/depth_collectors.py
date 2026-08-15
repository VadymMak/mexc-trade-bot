"""Depth collectors for the carry basket — perp via WS, spot via REST.

PERP (websocket, reusing the streams verified for ёрш on 2026-08-14):
  Gate : wss://fx-ws.gateio.ws/v4/ws/usdt
         {"channel":"futures.order_book","event":"subscribe",
          "payload":[contract, "10", "0"]}            -> full snapshot per change
  MEXC : wss://contract.mexc.com/edge
         {"method":"sub.depth.full","param":{"symbol":S,"limit":10}}
         MUST be sub.depth.full, NOT sub.depth — plain sub.depth sends unsorted
         incremental diffs and reading levels[0] as the touch yields nonsense
         (it made ONE_USDT look like a 444 bps market against a real 15 bps).

SPOT (REST polling ~2/s per symbol):
  Gate : GET /api/v4/spot/order_book?currency_pair=X_USDT&limit=10
  MEXC : GET /api/v3/depth?symbol=XUSDT&limit=10

WHY REST FOR SPOT: MEXC's spot websocket is protobuf-framed and `protobuf` is not
installed in researcher/.venv. Installing it would mutate the shared venv every
other collector runs on, for no benefit here — capacity measurement needs depth
LEVELS, not tick-by-tick queue dynamics, so 2 polls/sec is ample. Gate spot is
polled the same way purely for symmetry of treatment. Stated rather than hidden.
"""
from __future__ import annotations

import asyncio
import json
import logging
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
_RECV_TIMEOUT = _PING_INTERVAL * 3
_SPOT_POLL_SECS = 0.5


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


class _Base:
    def __init__(self, store: CarryBookStore, symbols: list[str]) -> None:
        self._store = store
        self._symbols = [s.upper() for s in symbols]
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


class GatePerpDepth(_Base):
    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._session()
            except Exception as exc:
                logger.warning("[carry/l2/gate-perp] %r — reconnect in %.0fs",
                               exc, _RECONNECT_DELAY)
            if not self._stop.is_set():
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _session(self) -> None:
        async with websockets.connect(_GATE_WS, max_size=4_194_304) as ws:
            for c in self._symbols:
                await ws.send(json.dumps({
                    "time": int(time.time()), "channel": "futures.order_book",
                    "event": "subscribe", "payload": [c, str(LEVELS), "0"],
                }))
            logger.info("[carry/l2/gate-perp] subscribed order_book(%d) for %d symbols",
                        LEVELS, len(self._symbols))
            hb = asyncio.create_task(self._hb(ws))
            try:
                while not self._stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("event") == "subscribe":
                        if msg.get("error"):
                            logger.warning("[carry/l2/gate-perp] sub error: %s", msg["error"])
                        continue
                    if msg.get("channel") != "futures.order_book":
                        continue
                    res = msg.get("result")
                    if not res:
                        continue
                    sym = res.get("contract") or res.get("s")
                    if not sym:
                        continue
                    await self._store.add_snapshot(
                        "gate", sym, "perp",
                        _levels(res.get("bids")), _levels(res.get("asks")), LEVELS)
            finally:
                hb.cancel()
                with suppress(Exception):
                    await hb

    async def _hb(self, ws) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(_PING_INTERVAL)
            with suppress(Exception):
                await ws.send(json.dumps({"time": int(time.time()),
                                          "channel": "futures.ping"}))


class MexcPerpDepth(_Base):
    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._session()
            except Exception as exc:
                logger.warning("[carry/l2/mexc-perp] %r — reconnect in %.0fs",
                               exc, _RECONNECT_DELAY)
            if not self._stop.is_set():
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _session(self) -> None:
        async with websockets.connect(_MEXC_WS, max_size=4_194_304) as ws:
            for s in self._symbols:
                await ws.send(json.dumps({"method": "sub.depth.full",
                                          "param": {"symbol": s, "limit": LEVELS}}))
            logger.info("[carry/l2/mexc-perp] subscribed depth.full(%d) for %d symbols",
                        LEVELS, len(self._symbols))
            hb = asyncio.create_task(self._hb(ws))
            try:
                while not self._stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("channel") != "push.depth.full":
                        continue
                    data = msg.get("data") or {}
                    sym = msg.get("symbol") or data.get("symbol")
                    if not sym:
                        continue
                    await self._store.add_snapshot(
                        "mexc", sym, "perp",
                        _levels(data.get("bids")), _levels(data.get("asks")), LEVELS)
            finally:
                hb.cancel()
                with suppress(Exception):
                    await hb

    async def _hb(self, ws) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(_PING_INTERVAL)
            with suppress(Exception):
                await ws.send(json.dumps({"method": "ping"}))


class SpotDepthPoller:
    """REST poller for spot books on both venues."""

    def __init__(self, store: CarryBookStore, basket: list[tuple[str, str]]) -> None:
        self._store = store
        self._basket = basket
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            while not self._stop.is_set():
                t0 = time.monotonic()
                await asyncio.gather(*(self._one(sess, ex, sym)
                                       for ex, sym in self._basket),
                                     return_exceptions=True)
                await asyncio.sleep(max(0.0, _SPOT_POLL_SECS - (time.monotonic() - t0)))

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
                    return
                d = await r.json()
        except Exception:
            return
        await self._store.add_snapshot(ex, sym, "spot",
                                       _levels(d.get("bids")),
                                       _levels(d.get("asks")), LEVELS)
