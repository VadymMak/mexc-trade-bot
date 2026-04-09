"""
Bybit linear-futures mark-price collector (V5 public WS).

Endpoint: wss://stream.bybit.com/v5/public/linear

Subscribe:
  {"op": "subscribe", "args": ["tickers.BTCUSDT", "tickers.ETHUSDT", …]}

Inbound tick:
  {
    "topic": "tickers.BTCUSDT",
    "ts":    1699000000000,
    "data":  {"markPrice": "29123.45", …}
  }

Heartbeat: send {"op": "ping"} every 20s; server replies {"op": "pong"}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Optional

import websockets

from .base_collector import BaseCollector

logger = logging.getLogger(__name__)

_WS_URL = "wss://stream.bybit.com/v5/public/linear"
_PING_INTERVAL = 20.0
_RECONNECT_DELAY = 5.0


def _to_bybit_sym(sym: str) -> str:
    """BTC_USDT → BTCUSDT"""
    return sym.replace("_", "").upper()


def _bybit_to_symbol(raw: str, known: list[str]) -> str:
    """BTCUSDT → BTC_USDT (matched against known list)."""
    return next((s for s in known if s.replace("_", "") == raw), raw)


class BybitCollector(BaseCollector):
    def __init__(self) -> None:
        super().__init__("bybit")
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[Bybit] Collector started for %s", self._symbols)

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None
        logger.info("[Bybit] Collector stopped")

    # ─── internals ───

    async def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[Bybit] %r — reconnecting in %.1fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _connect_and_stream(self) -> None:
        logger.info("[Bybit] Connecting → %s", _WS_URL)

        async with websockets.connect(_WS_URL, ping_interval=None, open_timeout=15) as ws:
            args = [f"tickers.{_to_bybit_sym(s)}" for s in self._symbols]
            await ws.send(json.dumps({"op": "subscribe", "args": args}))
            logger.info("[Bybit] Subscribed: %s", args)

            hb_task = asyncio.create_task(self._heartbeat(ws))
            try:
                while not self._stop_evt.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_PING_INTERVAL * 2)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    # Skip control frames and subscription acks
                    if msg.get("op") in {"pong", "subscribe"}:
                        continue

                    data = msg.get("data")
                    if not isinstance(data, dict):
                        continue

                    try:
                        price_str = data.get("markPrice")
                        if not price_str:
                            continue
                        price = float(price_str)
                        ts_ms = int(msg.get("ts", int(time.time() * 1000)))
                        # topic: "tickers.BTCUSDT"
                        topic = msg.get("topic", "")
                        raw_sym = topic.split(".")[-1]
                        symbol = _bybit_to_symbol(raw_sym, self._symbols)
                        await self._notify(symbol, price, ts_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
            finally:
                hb_task.cancel()
                with suppress(Exception):
                    await hb_task

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                with suppress(Exception):
                    await ws.send(json.dumps({"op": "ping"}))
        except asyncio.CancelledError:
            return
