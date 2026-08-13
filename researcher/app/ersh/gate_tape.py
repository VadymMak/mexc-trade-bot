"""Gate.io perp raw-tape collector.

Endpoint: wss://fx-ws.gateio.ws/v4/ws/usdt  (same as app/collectors/gate_collector.py)

Subscriptions:
  futures.trades       — one subscribe with the full contract list
  futures.book_ticker  — one subscribe per contract (best bid/ask on every change)

futures.trades payload (verified live 2026-08-13):
  {"id": 808078896, "size": 156, "create_time_ms": 1786627953130,
   "price": "63594.5", "contract": "BTC_USDT"}
  `size` is SIGNED: positive = taker BOUGHT (lifted the ask),
                    negative = taker SOLD (hit the bid).
  Verified against the book: positive prints landed at/above the ask 206× vs 34×
  at/below the bid; negative landed at/below the bid 103× vs 18× at/above the ask.

futures.book_ticker payload (verified live):
  {"t": 1786627952592, "u": ..., "s": "BTC_USDT",
   "b": "63594.4", "B": 52714, "a": "63594.5", "A": 7068}
  b/a = best bid/ask price, B/A = their sizes.

Heartbeat futures.ping every 20s; reconnect with 5s delay, infinite retries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress

import websockets

from .store import TapeStore

logger = logging.getLogger(__name__)

_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
_PING_INTERVAL = 20.0
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT = _PING_INTERVAL * 3
_QUOTE_MIN_INTERVAL = 1.0   # ≤1 book_ticker row per second per symbol


class GateTapeCollector:
    def __init__(self, store: TapeStore, symbols: list[str]) -> None:
        self._store = store
        self._symbols = [s.upper() for s in symbols]
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_quote: dict[str, float] = {}
        self._last_bbo: dict[str, tuple] = {}

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[ersh/gate] started for %d symbols", len(self._symbols))

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
                logger.warning("[ersh/gate] %r — reconnecting in %.0fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=_RECONNECT_DELAY)

    async def _stream(self) -> None:
        async with websockets.connect(
            _WS_URL, ping_interval=None, open_timeout=15, close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            await ws.send(json.dumps({
                "time": int(time.time()), "channel": "futures.trades",
                "event": "subscribe", "payload": self._symbols,
            }))
            for c in self._symbols:
                await ws.send(json.dumps({
                    "time": int(time.time()), "channel": "futures.book_ticker",
                    "event": "subscribe", "payload": [c],
                }))
            logger.info("[ersh/gate] subscribed trades+book_ticker for %d symbols",
                        len(self._symbols))

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
                            logger.warning("[ersh/gate] subscribe error: %s", msg["error"])
                        continue
                    if event in ("ping", "pong"):
                        continue

                    result = msg.get("result")
                    if not result:
                        continue

                    channel = msg.get("channel", "")
                    if channel == "futures.trades":
                        await self._on_trades(result)
                    elif channel == "futures.book_ticker":
                        await self._on_book_ticker(result)
            finally:
                hb.cancel()
                with suppress(Exception):
                    await hb

    async def _on_trades(self, result) -> None:
        items = result if isinstance(result, list) else [result]
        for it in items:
            try:
                raw_size = float(it["size"])
                if raw_size == 0:
                    continue          # no direction → skip rather than guess
                side = "buy" if raw_size > 0 else "sell"
                await self._store.add_print(
                    "gate", it.get("contract", ""), float(it["price"]),
                    abs(raw_size), side, it.get("create_time_ms"))
            except (KeyError, ValueError, TypeError):
                continue

    async def _on_book_ticker(self, result) -> None:
        items = result if isinstance(result, list) else [result]
        for r in items:
            try:
                symbol = r.get("s", "")
                bid, ask = float(r["b"]), float(r["a"])
            except (KeyError, ValueError, TypeError):
                continue
            if not symbol or bid <= 0 or ask <= 0:
                continue

            now = time.monotonic()
            if now - self._last_quote.get(symbol, 0.0) < _QUOTE_MIN_INTERVAL:
                continue
            if self._last_bbo.get(symbol) == (bid, ask):
                continue
            self._last_quote[symbol] = now
            self._last_bbo[symbol] = (bid, ask)
            await self._store.add_quote("gate", symbol, bid, ask, r.get("t"))

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
