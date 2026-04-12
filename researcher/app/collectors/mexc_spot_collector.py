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

Use: compare mexc_spot price vs mexc futures price per tick → basis = (futures - spot) / spot
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

_WS_URL          = "wss://wbs.mexc.com/ws"
_PING_INTERVAL   = 20.0
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT    = _PING_INTERVAL * 3


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
    """

    def __init__(self) -> None:
        super().__init__("mexc_spot")
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[MEXC-Spot] Collector started for %d symbols", len(self._symbols))

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[MEXC-Spot] %r — reconnecting in %.1fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _connect_and_stream(self) -> None:
        logger.info("[MEXC-Spot] Connecting → %s", _WS_URL)

        async with websockets.connect(
            _WS_URL,
            ping_interval=None,
            open_timeout=15,
            close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            # Subscribe in batches of 30 (MEXC limit per message)
            spot_syms = [_to_spot_sym(s) for s in self._symbols]
            batch_size = 30
            for i in range(0, len(spot_syms), batch_size):
                batch = spot_syms[i:i + batch_size]
                params = [f"spot@public.miniTicker.v3.api@{s}" for s in batch]
                sub = {"method": "SUBSCRIPTION", "params": params}
                await ws.send(json.dumps(sub))

            logger.info("[MEXC-Spot] Subscribed spot tickers: %d symbols", len(spot_syms))

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
                        symbol = _from_spot_sym(sym_ws)   # back to BTC_USDT format
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
