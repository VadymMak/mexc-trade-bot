"""
Gate.io futures mark-price collector.

Adapted from backend/app/market_data/gate_ws.py (spot), adjusted for:
  • Futures WS endpoint: wss://fx-ws.gateio.ws/v4/ws/usdt
  • Channel: futures.tickers
  • Field: result.mark_price

Subscribe payload: list of contract names, e.g. ["BTC_USDT", "ETH_USDT"]

Inbound event:
  {
    "time":    1699000000,
    "time_ms": 1699000000123,
    "channel": "futures.tickers",
    "event":   "update",
    "result":  {"contract": "BTC_USDT", "mark_price": "29123.45", …}
  }

Heartbeat: send futures.ping frame every 20s.
Reconnect: on any error with 5s delay, infinite retries.
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

_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
_PING_INTERVAL = 20.0
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT = _PING_INTERVAL * 3


def _to_gate_contract(sym: str) -> str:
    """Normalize to Gate.io contract format (underscore-separated, uppercase).

    BTC_USDT → BTC_USDT   BTCUSDT → BTC_USDT
    """
    s = sym.upper().replace("-", "_")
    if "_" in s:
        return s
    for quote in ("USDT", "USD", "BTC", "ETH", "USDC"):
        if s.endswith(quote):
            return f"{s[:-len(quote)]}_{quote}"
    return s


def _contract_to_symbol(contract: str, known: list[str]) -> str:
    """BTC_USDT → BTC_USDT (matched against known list)."""
    return next((s for s in known if _to_gate_contract(s) == contract), contract)


class GateCollector(BaseCollector):
    def __init__(self) -> None:
        super().__init__("gate")
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[Gate] Collector started for %s", self._symbols)

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None
        logger.info("[Gate] Collector stopped")

    # ─── internals ───

    async def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[Gate] %r — reconnecting in %.1fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _connect_and_stream(self) -> None:
        logger.info("[Gate] Connecting → %s", _WS_URL)

        async with websockets.connect(
            _WS_URL,
            ping_interval=None,
            open_timeout=15,
            close_timeout=5,
            max_size=4_194_304,  # 4 MB
        ) as ws:
            contracts = [_to_gate_contract(s) for s in self._symbols]
            sub = {
                "time": int(time.time()),
                "channel": "futures.tickers",
                "event": "subscribe",
                "payload": contracts,
            }
            await ws.send(json.dumps(sub))
            logger.info("[Gate] Subscribed futures.tickers: %s", contracts)

            hb_task = asyncio.create_task(self._heartbeat(ws))
            try:
                while not self._stop_evt.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        # Quiet period — send a soft ping and continue
                        with suppress(Exception):
                            await ws.ping()
                        continue

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    # Skip control frames
                    event = msg.get("event", "")
                    if event in {"ping", "pong", "subscribe"}:
                        continue

                    if msg.get("channel") != "futures.tickers":
                        continue

                    result = msg.get("result")
                    if not result:
                        continue

                    ts_ms = int(msg.get("time_ms", int(time.time() * 1000)))
                    items = result if isinstance(result, list) else [result]

                    for item in items:
                        try:
                            price_str = item.get("mark_price")
                            if not price_str:
                                continue
                            price = float(price_str)
                            contract = item.get("contract", "")
                            symbol = _contract_to_symbol(contract, self._symbols)
                            await self._notify(symbol, price, ts_ms)
                        except (ValueError, TypeError):
                            continue
            finally:
                hb_task.cancel()
                with suppress(Exception):
                    await hb_task

    async def _heartbeat(self, ws) -> None:
        """Send Gate.io futures ping frame every PING_INTERVAL seconds."""
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                ping = {
                    "time": int(time.time()),
                    "channel": "futures.ping",
                    "event": "",
                    "payload": [],
                }
                with suppress(Exception):
                    await ws.send(json.dumps(ping))
        except asyncio.CancelledError:
            return
