"""Gate.io perp top-5 order-book collector.

Endpoint: wss://fx-ws.gateio.ws/v4/ws/usdt   (same as app/ersh/gate_tape.py)

Subscription (Gate needs one subscribe per contract):
  {"channel": "futures.order_book", "event": "subscribe",
   "payload": [contract, "5", "0"]}            → limit 5, accuracy 0

futures.order_book result (full snapshot on every change):
  {"t": 1786..., "contract": "LA_USDT",
   "bids": [{"p": "0.3216", "s": 4210}, ...],
   "asks": [{"p": "0.3217", "s": 1880}, ...]}
`s` is CONTRACTS; USD value needs the quanto_multiplier (applied in l2_store).

Heartbeat futures.ping every 20s; reconnect with 5s delay, infinite retries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress

import websockets

from .l2_store import L2BookStore

logger = logging.getLogger(__name__)

_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
_PING_INTERVAL = 20.0
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT = _PING_INTERVAL * 3
_LEVELS = 5


def _parse_levels(levels) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for lvl in levels or []:
        try:
            if isinstance(lvl, dict):
                out.append((float(lvl["p"]), float(lvl["s"])))
            elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                out.append((float(lvl[0]), float(lvl[1])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


class GateL2Collector:
    def __init__(self, store: L2BookStore, symbols: list[str]) -> None:
        self._store = store
        self._symbols = [s.upper() for s in symbols]
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[ersh/l2/gate] started for %d symbols", len(self._symbols))

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[ersh/l2/gate] %r — reconnecting in %.0fs",
                               exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=_RECONNECT_DELAY)

    async def _stream(self) -> None:
        async with websockets.connect(
            _WS_URL, ping_interval=None, open_timeout=15, close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            for c in self._symbols:
                await ws.send(json.dumps({
                    "time": int(time.time()), "channel": "futures.order_book",
                    "event": "subscribe", "payload": [c, str(_LEVELS), "0"],
                }))
            logger.info("[ersh/l2/gate] subscribed order_book(limit=%d) for %d symbols",
                        _LEVELS, len(self._symbols))

            hb = asyncio.create_task(self._heartbeat(ws))
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

                    event = msg.get("event", "")
                    if event == "subscribe":
                        if msg.get("error"):
                            logger.warning("[ersh/l2/gate] subscribe error: %s", msg["error"])
                        continue
                    if event in ("ping", "pong"):
                        continue

                    result = msg.get("result")
                    if result and msg.get("channel") == "futures.order_book":
                        await self._on_book(result)
            finally:
                hb.cancel()
                with suppress(Exception):
                    await hb

    async def _on_book(self, result) -> None:
        items = result if isinstance(result, list) else [result]
        for r in items:
            if not isinstance(r, dict):
                continue
            symbol = r.get("contract", "") or r.get("s", "")
            bids = _parse_levels(r.get("bids"))
            asks = _parse_levels(r.get("asks"))
            if not symbol or not bids or not asks:
                continue
            bids.sort(key=lambda x: -x[0])
            asks.sort(key=lambda x: x[0])
            if bids[0][0] >= asks[0][0]:
                continue               # crossed/locked book — never record it
            await self._store.add_snapshot("gate", symbol, bids, asks, r.get("t"))

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                with suppress(Exception):
                    await ws.send(json.dumps({
                        "time": int(time.time()), "channel": "futures.ping",
                        "event": "", "payload": [],
                    }))
        except asyncio.CancelledError:
            return
