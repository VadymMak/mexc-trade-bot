"""MEXC perp raw-tape collector.

Endpoint: wss://contract.mexc.com/edge   (same as app/collectors/mexc_collector.py)

Subscriptions per symbol:
  {"method": "sub.deal",   "param": {"symbol": "X_USDT"}}          → push.deal
  {"method": "sub.ticker", "param": {"symbol": "X_USDT"}}          → push.ticker

push.deal payload (verified live 2026-08-13):
  {"p": 63589.7, "v": 2925, "T": 2, "O": 3, "M": 2, "t": 1786627952006, "i": "..."}
  T = aggressor side: 1 = BUY (taker lifted the ask), 2 = SELL (taker hit the bid).
  Verified against the book: T=1 prints landed at/above the ask 51× vs 21× at/below
  the bid; T=2 landed at/below the bid 74× vs 20× at/above the ask.

push.ticker carries bid1/ask1 — used as the best-bid/ask source (cheaper than
sub.depth, which sends full 5-level snapshots we don't need here).

Heartbeat {"method": "ping"} every 20s; reconnect with 5s delay, infinite retries.
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

_WS_URL = "wss://contract.mexc.com/edge"
_PING_INTERVAL = 20.0
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT = _PING_INTERVAL * 3
_QUOTE_MIN_INTERVAL = 1.0   # ≤1 book_ticker row per second per symbol


class MexcTapeCollector:
    def __init__(self, store: TapeStore, symbols: list[str]) -> None:
        self._store = store
        self._symbols = [s.upper() for s in symbols]
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_quote: dict[str, float] = {}   # symbol → monotonic of last write
        self._last_bbo: dict[str, tuple] = {}     # symbol → (bid, ask) last written

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[ersh/mexc] started for %d symbols", len(self._symbols))

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
                logger.warning("[ersh/mexc] %r — reconnecting in %.0fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=_RECONNECT_DELAY)

    async def _stream(self) -> None:
        async with websockets.connect(
            _WS_URL, ping_interval=None, open_timeout=15, close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            for sym in self._symbols:
                await ws.send(json.dumps({"method": "sub.deal", "param": {"symbol": sym}}))
                await ws.send(json.dumps({"method": "sub.ticker", "param": {"symbol": sym}}))
            logger.info("[ersh/mexc] subscribed deals+ticker for %d symbols", len(self._symbols))

            hb = asyncio.create_task(self._heartbeat(ws))
            try:
                while not self._stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        with suppress(Exception):
                            await ws.ping()
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    channel = msg.get("channel", "")
                    if channel == "push.deal":
                        await self._on_deals(msg)
                    elif channel == "push.ticker":
                        await self._on_ticker(msg)
            finally:
                hb.cancel()
                with suppress(Exception):
                    await hb

    async def _on_deals(self, msg: dict) -> None:
        data = msg.get("data")
        symbol = msg.get("symbol") or (data.get("symbol", "") if isinstance(data, dict) else "")
        if isinstance(data, list):
            deals = data
        elif isinstance(data, dict):
            deals = data.get("deals") or ([data] if "p" in data else [])
            symbol = data.get("symbol", "") or symbol
        else:
            return
        if not symbol:
            return

        for d in deals:
            try:
                t = d.get("T")
                if t is None:
                    continue          # no aggressor side → don't guess, skip
                side = "buy" if int(t) == 1 else "sell"
                await self._store.add_print(
                    "mexc", symbol, float(d["p"]), float(d["v"]), side, d.get("t"))
            except (KeyError, ValueError, TypeError):
                continue

    async def _on_ticker(self, msg: dict) -> None:
        data = msg.get("data")
        if not isinstance(data, dict):
            return
        symbol = data.get("symbol", "")
        bid, ask = data.get("bid1"), data.get("ask1")
        if not symbol or bid is None or ask is None:
            return
        try:
            bid, ask = float(bid), float(ask)
        except (ValueError, TypeError):
            return
        if bid <= 0 or ask <= 0:
            return

        now = time.monotonic()
        if now - self._last_quote.get(symbol, 0.0) < _QUOTE_MIN_INTERVAL:
            return
        if self._last_bbo.get(symbol) == (bid, ask):
            return                     # unchanged top of book — nothing to record
        self._last_quote[symbol] = now
        self._last_bbo[symbol] = (bid, ask)
        await self._store.add_quote("mexc", symbol, bid, ask, data.get("timestamp"))

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                with suppress(Exception):
                    await ws.send(json.dumps({"method": "ping"}))
        except asyncio.CancelledError:
            return
