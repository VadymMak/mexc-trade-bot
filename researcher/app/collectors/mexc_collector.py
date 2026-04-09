"""
MEXC Futures mark-price collector.

Endpoint: wss://contract.mexc.com/edge

Subscribe per symbol:
  {"method": "sub.ticker", "param": {"symbol": "BTC_USDT"}}

Inbound tick:
  {
    "channel": "push.ticker",
    "data": {
      "symbol":    "BTC_USDT",
      "lastPrice": 29123.45,
      "fairPrice": 29120.00,
      "timestamp": 1699000000123
    },
    "ts": 1699000000123
  }

Heartbeat: {"method": "ping"} every 20s; server replies {"channel": "pong"}.
Reconnect: on any error with 5s delay, infinite retries.
Symbol format: BTC_USDT (underscore, same as internal format — no conversion needed).
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

_WS_URL = "wss://contract.mexc.com/edge"
_PING_INTERVAL = 20.0
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT = _PING_INTERVAL * 3


class MexcCollector(BaseCollector):
    def __init__(self) -> None:
        super().__init__("mexc")
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[MEXC] Collector started for %s", self._symbols)

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None
        logger.info("[MEXC] Collector stopped")

    # ─── internals ───

    async def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[MEXC] %r — reconnecting in %.1fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _connect_and_stream(self) -> None:
        logger.info("[MEXC] Connecting → %s", _WS_URL)

        async with websockets.connect(
            _WS_URL,
            ping_interval=None,
            open_timeout=15,
            close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            # Subscribe to each symbol individually
            for sym in self._symbols:
                sub = {"method": "sub.ticker", "param": {"symbol": sym}}
                await ws.send(json.dumps(sub))
            logger.info("[MEXC] Subscribed tickers: %s", self._symbols)

            hb_task = asyncio.create_task(self._heartbeat(ws))
            try:
                while not self._stop_evt.is_set():
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

                    # Skip control frames
                    if channel in {"pong", "sub.ticker"}:
                        continue

                    if channel != "push.ticker":
                        continue

                    data = msg.get("data")
                    if not isinstance(data, dict):
                        continue

                    try:
                        # Prefer fairPrice (mark price), fall back to lastPrice
                        price_raw = data.get("fairPrice") or data.get("lastPrice")
                        if price_raw is None:
                            continue
                        price = float(price_raw)
                        symbol = data.get("symbol", "")
                        if not symbol:
                            continue
                        ts_ms = int(
                            data.get("timestamp")
                            or msg.get("ts")
                            or int(time.time() * 1000)
                        )
                        await self._notify(symbol, price, ts_ms)
                    except (ValueError, TypeError):
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
                    await ws.send(json.dumps({"method": "ping"}))
        except asyncio.CancelledError:
            return
