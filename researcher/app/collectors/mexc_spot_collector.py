"""
MEXC Spot price collector — REST polling implementation.

Why REST instead of WebSocket:
  MEXC spot WS hard-closes unauthenticated connections every ~30s regardless
  of subscription count. REST polling every POLL_INTERVAL_S avoids this entirely.
  For spot-futures basis analysis tick precision is not needed — 5s updates suffice.

REST endpoint:
  GET https://api.mexc.com/api/v3/ticker/price
  Response: [{"symbol": "BTCUSDT", "price": "29123.45"}, ...]

Symbol conversion: BTC_USDT (internal) ↔ BTCUSDT (MEXC REST format, no underscore).
Exchange name reported as "mexc_spot" for SpreadMatrix spot_basis_pct computation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Optional

import aiohttp

from .base_collector import BaseCollector

logger = logging.getLogger(__name__)

_REST_URL         = "https://api.mexc.com/api/v3/ticker/price"
_POLL_INTERVAL_S  = 5.0    # seconds between REST polls
_REQUEST_TIMEOUT  = 8.0    # seconds per request
_RETRY_DELAY_S    = 10.0   # wait after request failure before retrying


def _to_spot_sym(symbol: str) -> str:
    """BTC_USDT → BTCUSDT"""
    return symbol.replace("_", "")


def _from_spot_sym(symbol: str) -> str:
    """BTCUSDT → BTC_USDT"""
    if symbol.endswith("USDT"):
        return symbol[:-4] + "_USDT"
    return symbol


class MexcSpotCollector(BaseCollector):
    """
    Polls MEXC Spot REST API every POLL_INTERVAL_S seconds.
    Reports prices under exchange name 'mexc_spot'.
    Used for spot-futures basis analysis (not for trading decisions).
    """

    def __init__(self) -> None:
        super().__init__("mexc_spot")
        self._symbols: list[str] = []
        self._spot_syms: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols   = [s.upper() for s in symbols]
        self._spot_syms = [_to_spot_sym(s) for s in self._symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "[MEXC-Spot] REST poller started for %d symbols (interval=%.0fs)",
            len(self._symbols), _POLL_INTERVAL_S,
        )

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None
        logger.info("[MEXC-Spot] REST poller stopped")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Poll MEXC REST every POLL_INTERVAL_S and emit price events."""
        # Build a set for fast lookup
        wanted = set(self._spot_syms)

        async with aiohttp.ClientSession() as session:
            while not self._stop_evt.is_set():
                try:
                    ts_ms = int(time.time() * 1000)
                    async with session.get(
                        _REST_URL,
                        timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(
                                "[MEXC-Spot] REST HTTP %d — retrying in %.0fs",
                                resp.status, _RETRY_DELAY_S,
                            )
                            await self._sleep(_RETRY_DELAY_S)
                            continue

                        data = await resp.json(content_type=None)

                    # data is a list of {"symbol": "BTCUSDT", "price": "29123.45"}
                    matched = 0
                    for item in data:
                        sym_ws = item.get("symbol", "")
                        if sym_ws not in wanted:
                            continue
                        try:
                            price  = float(item["price"])
                            symbol = _from_spot_sym(sym_ws)
                            await self._notify(symbol, price, ts_ms)
                            matched += 1
                        except (ValueError, TypeError, KeyError):
                            continue

                    logger.debug("[MEXC-Spot] Polled %d/%d symbols OK", matched, len(wanted))

                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning("[MEXC-Spot] Poll error: %r — retrying in %.0fs", exc, _RETRY_DELAY_S)
                    await self._sleep(_RETRY_DELAY_S)
                    continue

                await self._sleep(_POLL_INTERVAL_S)

    async def _sleep(self, seconds: float) -> None:
        """Sleep that can be interrupted by stop event."""
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_evt.wait(), timeout=seconds)
