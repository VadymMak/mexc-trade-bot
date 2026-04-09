"""
Binance futures mark-price collector.

Connects to the combined stream endpoint:
  wss://fstream.binance.com/stream?streams=btcusdt@markPrice/ethusdt@markPrice/…

Message format (per stream):
  {
    "stream": "btcusdt@markPrice",
    "data": {
      "p": "29123.45",   # mark price (string)
      "T": 1699000000000 # event time ms
    }
  }
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Optional

import websockets

from .base_collector import BaseCollector

logger = logging.getLogger(__name__)

_WS_BASE = "wss://fstream.binance.com/stream"
_PING_INTERVAL = 20.0   # seconds
_RECONNECT_DELAY = 5.0  # seconds


def _to_stream_name(sym: str) -> str:
    """BTC_USDT → btcusdt@markPrice"""
    return sym.replace("_", "").lower() + "@markPrice"


def _raw_to_symbol(raw: str, known: list[str]) -> str:
    """btcusdt → BTC_USDT (matched against known list)."""
    upper = raw.upper()
    return next((s for s in known if s.replace("_", "") == upper), upper)


class BinanceCollector(BaseCollector):
    def __init__(self) -> None:
        super().__init__("binance")
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[Binance] Collector started for %s", self._symbols)

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None
        logger.info("[Binance] Collector stopped")

    # ─── internals ───

    async def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[Binance] %r — reconnecting in %.1fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _connect_and_stream(self) -> None:
        streams = "/".join(_to_stream_name(s) for s in self._symbols)
        url = f"{_WS_BASE}?streams={streams}"
        logger.info("[Binance] Connecting → %s", url)

        async with websockets.connect(url, ping_interval=None, open_timeout=15) as ws:
            ping_task = asyncio.create_task(self._ping_loop(ws))
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

                    data = msg.get("data")
                    if not isinstance(data, dict):
                        continue

                    try:
                        price = float(data["p"])
                        ts_ms = int(data["T"])
                        stream = msg.get("stream", "")
                        raw_sym = stream.split("@")[0]   # "btcusdt"
                        symbol = _raw_to_symbol(raw_sym, self._symbols)
                        await self._notify(symbol, price, ts_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
            finally:
                ping_task.cancel()
                with suppress(Exception):
                    await ping_task

    async def _ping_loop(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                with suppress(Exception):
                    await ws.ping()
        except asyncio.CancelledError:
            return
