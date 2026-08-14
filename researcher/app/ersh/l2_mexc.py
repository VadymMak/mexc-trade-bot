"""MEXC perp top-5 order-book collector.

Endpoint: wss://contract.mexc.com/edge   (same as app/ersh/mexc_tape.py)

Subscription per symbol:
  {"method": "sub.depth.full", "param": {"symbol": "X_USDT", "limit": 5}}
                                                            → push.depth.full

It MUST be `sub.depth.full`, not `sub.depth`. Verified live 2026-08-14 against
GET /api/v1/contract/depth/ONE_USDT:

  sub.depth      → incremental diffs. The `limit` param is ignored: messages
                   carry whatever levels just changed, unsorted, mostly far from
                   the touch (one message held 25 bid levels spanning 0.000609 …
                   0.000635 in no order). Reading levels[0] as the touch yields
                   nonsense — it made ONE_USDT look like a 444 bps market when
                   the real spread was 15 bps.
  sub.depth.full → true top-5 snapshot, best-first, matching REST exactly
                   (bid 0.000636 / ask 0.000643).

NOTE: researcher/app/core/market_flow.py subscribes `sub.depth` with a limit and
treats the payload as a snapshot, so its MEXC book_imbalance is computed from
diffs. Not touched here — flagged for a separate fix.

push.depth.full payload:
  {"channel": "push.depth.full", "symbol": "X_USDT", "ts": 1786...,
   "data": {"bids": [[price, vol, order_count], ...], "asks": [...], "version": N}}
Levels also occur as {"p": price, "v": vol} dicts on some symbols — both parsed.
Consecutive snapshots often repeat unchanged (only `version` moves); the store's
dedupe drops those.

Heartbeat {"method": "ping"} every 20s; reconnect with 5s delay, infinite retries.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

import websockets

from .l2_store import L2BookStore

logger = logging.getLogger(__name__)

_WS_URL = "wss://contract.mexc.com/edge"
_PING_INTERVAL = 20.0
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT = _PING_INTERVAL * 3
_LEVELS = 5


def _parse_levels(levels) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for lvl in levels or []:
        try:
            if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                out.append((float(lvl[0]), float(lvl[1])))
            elif isinstance(lvl, dict):
                p = lvl.get("p", lvl.get("price"))
                s = lvl.get("v", lvl.get("size", lvl.get("quantity")))
                if p is None or s is None:
                    continue
                out.append((float(p), float(s)))
        except (ValueError, TypeError):
            continue
    return out


class MexcL2Collector:
    def __init__(self, store: L2BookStore, symbols: list[str]) -> None:
        self._store = store
        self._symbols = [s.upper() for s in symbols]
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[ersh/l2/mexc] started for %d symbols", len(self._symbols))

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
                logger.warning("[ersh/l2/mexc] %r — reconnecting in %.0fs",
                               exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=_RECONNECT_DELAY)

    async def _stream(self) -> None:
        async with websockets.connect(
            _WS_URL, ping_interval=None, open_timeout=15, close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            for sym in self._symbols:
                await ws.send(json.dumps({
                    "method": "sub.depth.full",
                    "param": {"symbol": sym, "limit": _LEVELS},
                }))
            logger.info("[ersh/l2/mexc] subscribed depth.full(limit=%d) for %d symbols",
                        _LEVELS, len(self._symbols))

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
                    if isinstance(msg, list):
                        msg = msg[0] if len(msg) == 1 and isinstance(msg[0], dict) else None
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("channel") == "push.depth.full":
                        await self._on_depth(msg)
            finally:
                hb.cancel()
                with suppress(Exception):
                    await hb

    async def _on_depth(self, msg: dict) -> None:
        data = msg.get("data")
        if not isinstance(data, dict):
            return
        symbol = msg.get("symbol") or data.get("symbol", "")
        if not symbol:
            return
        bids = _parse_levels(data.get("bids"))
        asks = _parse_levels(data.get("asks"))
        if not bids or not asks:
            return
        # Defensive ordering — level 1 must be the touch whatever the feed sends.
        bids.sort(key=lambda x: -x[0])
        asks.sort(key=lambda x: x[0])
        if bids[0][0] >= asks[0][0]:
            return                     # crossed/locked book — never record it
        await self._store.add_snapshot("mexc", symbol, bids, asks,
                                       msg.get("ts") or data.get("ts"))

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                with suppress(Exception):
                    await ws.send(json.dumps({"method": "ping"}))
        except asyncio.CancelledError:
            return
