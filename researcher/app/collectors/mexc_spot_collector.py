"""
MEXC Spot price collector.

Endpoint: wss://wbs.mexc.com/ws

Subscribe per symbol:
  {"method": "SUBSCRIPTION", "params": ["spot@public.miniTicker.v3.api@BTCUSDT"]}

Inbound tick:
  {
    "c": "spot@public.miniTicker.v3.api@BTCUSDT",
    "d": {
      "s": "BTCUSDT",
      "p": "29123.45",   ← last price
      "o": ..., "h": ..., "l": ..., "v": ...
    },
    "t": 1699000000123
  }

Symbol conversion: BTC_USDT (internal) → BTCUSDT (spot WS format, no underscore).
Exchange name reported as "mexc_spot" to distinguish from futures "mexc".

MEXC WS connection limit: max ~30 streams per connection before server
force-closes (ConnectionClosedOK 1005).  We split symbols across multiple
parallel connections of MAX_STREAMS_PER_CONN each.
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

_WS_URL              = "wss://wbs.mexc.com/ws"
_PING_INTERVAL       = 20.0
_RECONNECT_DELAY     = 5.0
_RECV_TIMEOUT        = _PING_INTERVAL * 3
# MEXC force-closes connections with >30 streams — cap well below limit
MAX_STREAMS_PER_CONN = 25


def _to_spot_sym(symbol: str) -> str:
    """BTC_USDT → BTCUSDT"""
    return symbol.replace("_", "")


def _from_spot_sym(symbol: str) -> str:
    """BTCUSDT → BTC_USDT  (best-effort: split before USDT)"""
    if symbol.endswith("USDT"):
        return symbol[:-4] + "_USDT"
    return symbol


class MexcSpotCollector(BaseCollector):
    """
    Streams MEXC spot last-price ticks for the given symbols.
    Reports prices under exchange name 'mexc_spot'.

    Automatically splits symbols across multiple parallel WS connections
    (MAX_STREAMS_PER_CONN symbols each) to stay within MEXC's connection limit.
    """

    def __init__(self) -> None:
        super().__init__("mexc_spot")
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()

        # Split into chunks of MAX_STREAMS_PER_CONN
        chunks = [
            self._symbols[i:i + MAX_STREAMS_PER_CONN]
            for i in range(0, len(self._symbols), MAX_STREAMS_PER_CONN)
        ]
        self._tasks = [
            asyncio.create_task(self._run_shard(idx, chunk))
            for idx, chunk in enumerate(chunks)
        ]
        logger.info(
            "[MEXC-Spot] Collector started for %d symbols across %d connections",
            len(self._symbols), len(chunks),
        )

    async def disconnect(self) -> None:
        self._stop_evt.set()
        for t in self._tasks:
            t.cancel()
            with suppress(Exception):
                await t
        self._tasks.clear()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run_shard(self, idx: int, symbols: list[str]) -> None:
        """Run one WS connection for a subset of symbols."""
        tag = f"[MEXC-Spot#{idx}]"
        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream(tag, symbols)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("%s %r — reconnecting in %.1fs", tag, exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _connect_and_stream(self, tag: str, symbols: list[str]) -> None:
        logger.debug("%s Connecting → %s (%d syms)", tag, _WS_URL, len(symbols))

        async with websockets.connect(
            _WS_URL,
            ping_interval=None,
            open_timeout=15,
            close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            # Subscribe all symbols in one message (≤ MAX_STREAMS_PER_CONN)
            spot_syms = [_to_spot_sym(s) for s in symbols]
            params = [f"spot@public.miniTicker.v3.api@{s}" for s in spot_syms]
            await ws.send(json.dumps({"method": "SUBSCRIPTION", "params": params}))
            logger.info("%s Subscribed %d spot tickers", tag, len(spot_syms))

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

                    # Skip subscription confirmations
                    if msg.get("msg") or not msg.get("d"):
                        continue

                    channel = msg.get("c", "")
                    if "miniTicker" not in channel:
                        continue

                    data = msg.get("d", {})
                    try:
                        price_raw = data.get("p")  # last price
                        if price_raw is None:
                            continue
                        price  = float(price_raw)
                        sym_ws = data.get("s", "")
                        if not sym_ws:
                            continue
                        symbol = _from_spot_sym(sym_ws)
                        ts_ms  = int(msg.get("t") or int(time.time() * 1000))
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
                    await ws.send(json.dumps({"method": "PING"}))
        except asyncio.CancelledError:
            return
