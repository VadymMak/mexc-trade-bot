from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Any, Dict, List, Optional, Sequence, Tuple

import websockets

from app.config.settings import settings
from app.services.book_tracker import on_book_ticker, on_partial_depth

logger = logging.getLogger(__name__)


# ------------------------------ helpers ------------------------------

def _is_demo_mode() -> bool:
    try:
        m = getattr(settings, "active_mode", None) or getattr(settings, "account_mode", None) or "paper"
        return str(m).lower() in {"paper", "demo", "test", "testnet"}
    except Exception:
        return True


def _ws_base_url() -> str:
    """
    Gate official WS bases:
      - live:    wss://api.gateio.ws/ws/v4/
      - testnet: wss://api-public.sandbox.gateio.ws/ws/v4/
    Use settings overrides if provided.
    """
    if _is_demo_mode():
        return getattr(settings, "gate_testnet_ws_base", None) or "wss://api-public.sandbox.gateio.ws/ws/v4/"
    return getattr(settings, "gate_ws_base", None) or "wss://api.gateio.ws/ws/v4/"


def _to_gate_pair(sym: str) -> str:
    s = (sym or "").upper().strip()
    if "_" in s:
        return s
    for q in ("USDT", "USD", "BTC", "ETH"):
        if s.endswith(q):
            base = s[: -len(q)]
            return f"{base}_{q}"
    if len(s) > 4:
        return f"{s[:-4]}_{s[-4:]}"
    return s


# ------------------------------ client ------------------------------

class GateWebSocketClient:
    """
    Public Gate Spot WS client:
      • Subscribes to `spot.tickers` (L1) and `spot.order_book` (L2 snapshots).
      • Normalizes events and forwards them to book_tracker.
      • Reconnects with exponential backoff; graceful stop().

    NOTE:
      • No auth here (public data only).
      • For very high-rate L2 you can later add `spot.order_book_update` deltas;
        this version keeps snapshots for simplicity.
    """

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        depth_limit: int = 10,
        want_tickers: bool = True,
        want_order_book: bool = True,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
    ) -> None:
        uniq: List[str] = []
        seen = set()
        for s in symbols:
            u = (s or "").upper().strip()
            if u and u not in seen:
                uniq.append(u)
                seen.add(u)

        self.symbols = uniq
        self.depth_limit = max(5, min(int(depth_limit or 10), 50))
        self.want_tickers = bool(want_tickers)
        self.want_order_book = bool(want_order_book)

        self._ws_url = _ws_base_url()
        self._stop_evt = asyncio.Event()
        self._conn: Optional[websockets.WebSocketClientProtocol] = None

        # backoff
        self._attempt = 0

        # ping handling (we still let the library ping as well)
        self._ping_interval = float(ping_interval)
        self._ping_timeout = float(ping_timeout)
        self._hb_task: Optional[asyncio.Task] = None

        # recv timeout (when exceeded we just ping and continue)
        self._recv_timeout = max(self._ping_interval + self._ping_timeout + 5.0, 45.0)

    # ------------- public -------------

    async def run(self) -> None:
        """
        Long-running connection loop. Call inside a task.
        """
        if not self.symbols:
            logger.info("Gate WS: no symbols to subscribe; exiting.")
            return

        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
                # connection closed normally (stop was likely requested)
                self._attempt = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._attempt += 1
                delay = min(30.0, 1.0 * (2 ** min(self._attempt, 5)))
                logger.warning(f"Gate WS error: {e!r} — reconnect in {delay:.1f}s")
                try:
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

        await self._cleanup()

    async def stop(self) -> None:
        """
        Signal the run-loop to stop and close the socket.
        """
        self._stop_evt.set()
        await self._cleanup()

    # ------------- internals -------------

    async def _cleanup(self) -> None:
        if self._hb_task:
            self._hb_task.cancel()
            with suppress(Exception):
                await self._hb_task
            self._hb_task = None
        if self._conn:
            with suppress(Exception):
                await self._conn.close(code=1000)
        self._conn = None

    async def _connect_and_stream(self) -> None:
        url = self._ws_url
        logger.info(f"Gate WS connecting → {url} symbols={self.symbols} depth_limit={self.depth_limit}")

        async with websockets.connect(
            url,
            ping_interval=self._ping_interval,   # library-level ping
            ping_timeout=self._ping_timeout,
            close_timeout=5,
            max_size=2**22,  # ~4MB frames
        ) as ws:
            self._conn = ws

            # Subscribe once connected
            await self._subscribe(ws)

            # Optional app-level heartbeat (send ping frames)
            self._hb_task = asyncio.create_task(self._heartbeat(ws))

            # Main loop
            while not self._stop_evt.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=self._recv_timeout)
                except asyncio.TimeoutError:
                    # Quiet period: keep connection alive with a ping and continue (no reconnect)
                    logger.debug("Gate WS recv timeout — sending ping and continuing")
                    with suppress(Exception):
                        await ws.ping()
                    continue

                if not msg:
                    continue
                try:
                    data = json.loads(msg)
                except Exception:
                    continue

                # Ignore explicit ping/pong event payloads
                if isinstance(data, dict) and data.get("event") in {"ping", "pong"}:
                    continue

                await self._handle_message(data)

    async def _heartbeat(self, ws: websockets.WebSocketClientProtocol) -> None:
        # Gate handles ping/pong automatically, but a soft keepalive is fine
        try:
            while not self._stop_evt.is_set():
                await asyncio.sleep(self._ping_interval)
                with suppress(Exception):
                    await ws.ping()
        except asyncio.CancelledError:
            return

    async def _subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        """
        Gate v4 WS expects messages:
          spot.tickers     : {"time": <sec>, "channel":"spot.tickers", "event":"subscribe", "payload":[<pair>...]}
          spot.order_book  : {"time": <sec>, "channel":"spot.order_book","event":"subscribe","payload":[<pair>, "<limit>", "100ms"]}
        """
        pairs = [_to_gate_pair(s) for s in self.symbols]

        # L1 tickers — batch payload
        if self.want_tickers and pairs:
            sub = {
                "time": int(time.time()),
                "channel": "spot.tickers",
                "event": "subscribe",
                "payload": pairs,
            }
            with suppress(Exception):
                await ws.send(json.dumps(sub))

        # L2 order_book — one message per pair
        if self.want_order_book and pairs:
            for p in pairs:
                sub = {
                    "time": int(time.time()),
                    "channel": "spot.order_book",
                    "event": "subscribe",
                    "payload": [p, str(self.depth_limit), "100ms"],
                }
                with suppress(Exception):
                    await ws.send(json.dumps(sub))

    async def _handle_message(self, data: Any) -> None:
        """
        Expected shapes:
          {"time":..., "channel":"spot.tickers",    "event":"update", "result": {...} OR [ {...}, ... ]}
          {"time":..., "channel":"spot.order_book", "event":"update", "result": {...}}
        """
        if not isinstance(data, dict):
            return

        channel = data.get("channel")
        event = data.get("event")
        result = data.get("result") or data.get("payload") or data.get("data")

        if event not in {"update", "subscribe"}:
            return

        if channel == "spot.tickers":
            await self._handle_ticker_result(result)
        elif channel == "spot.order_book":
            await self._handle_order_book_result(result)

    # ----- handlers -----

    async def _handle_ticker_result(self, result: Any) -> None:
        items: List[Dict[str, Any]] = []
        if isinstance(result, dict):
            items = [result]
        elif isinstance(result, list):
            items = [x for x in result if isinstance(x, dict)]

        if not items:
            return

        now_ms = int(time.time() * 1000)
        for it in items:
            pair = str(it.get("currency_pair") or it.get("s") or "").upper()
            if not pair:
                continue
            sym = pair.replace("_", "")

            bid = _to_float(it.get("highest_bid") or it.get("b") or 0.0)
            ask = _to_float(it.get("lowest_ask") or it.get("a") or 0.0)

            # Gate L1 sizes are often not provided → 0.0 (depth will fill)
            with suppress(Exception):
                await on_book_ticker(sym, bid, 0.0, ask, 0.0, ts_ms=now_ms)

    async def _handle_order_book_result(self, result: Any) -> None:
        if isinstance(result, list):
            # occasionally comes as a list of one
            result = result[0] if result and isinstance(result[0], dict) else None

        if not isinstance(result, dict):
            return

        pair = str(result.get("currency_pair") or result.get("s") or "").upper()
        if not pair:
            return
        sym = pair.replace("_", "")

        bids_raw = result.get("bids") or []
        asks_raw = result.get("asks") or []

        bids, asks = _parse_levels(bids_raw), _parse_levels(asks_raw)
        if not bids and not asks:
            return

        ts_ms = int(time.time() * 1000)
        with suppress(Exception):
            await on_partial_depth(sym, bids[: self.depth_limit], asks[: self.depth_limit], ts_ms=ts_ms)


# ------------------------------ utils ------------------------------

def _to_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _parse_levels(raw: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        try:
            p = float(row[0]); q = float(row[1])
            if p > 0 and q > 0:
                out.append((p, q))
        except Exception:
            continue
    return out
