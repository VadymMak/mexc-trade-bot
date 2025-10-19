# app/market_data/gate_ws.py
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

# Best-effort: enable depth on the real tracker when available
try:
    from app.market_data.book_tracker import book_tracker as _BOOK_TRACKER
except Exception:
    _BOOK_TRACKER = None  # type: ignore

logger = logging.getLogger(__name__)


# ───────────────────────── helpers ─────────────────────────

def _to_gate_pair(sym: str) -> str:
    """Convert symbol to Gate.io pair format (e.g., BTCUSDT → BTC_USDT)."""
    s = (sym or "").upper().strip()
    if "_" in s:
        return s
    for q in ("USDT", "USD", "BTC", "ETH"):
        if s.endswith(q):
            return f"{s[:-len(q)]}_{q}"
    if len(s) > 4:
        return f"{s[:-4]}_{s[-4:]}"
    return s


def _to_float(x: Any) -> float:
    """Safe float conversion."""
    try:
        return float(x)
    except Exception:
        return 0.0


def _parse_levels(raw: Any) -> List[Tuple[float, float]]:
    """Parse order book levels from Gate.io format: [[price, qty], ...]"""
    out: List[Tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        try:
            p = float(row[0])
            q = float(row[1])
            if p > 0 and q > 0:
                out.append((p, q))
        except Exception:
            continue
    return out


# ───────────────────────── client ─────────────────────────

class GateWebSocketClient:
    """
    Gate.io Public Spot WebSocket Client (Settings-Integrated)
    
    Features:
    • Subscribes to spot.tickers (L1) and spot.order_book (L2 snapshots)
    • Uses centralized settings for all configuration
    • Exponential backoff reconnection using REST retry settings
    • Graceful shutdown and cleanup
    • Forwards normalized events to book_tracker
    
    Settings Used:
    • ws_base_url_resolved: Auto-detects live/testnet based on active_mode
    • ws_ping_interval_sec, ws_ping_timeout: Ping/pong timing
    • ws_recv_timeout_multiplier: Recv timeout calculation
    • gate_depth_limit: Order book depth (5-50)
    • rest_retry_backoff_ms, rest_retry_backoff_factor, rest_backoff_max_sec: Reconnection backoff
    """

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        depth_limit: Optional[int] = None,
        want_tickers: bool = True,
        want_order_book: bool = True,
        ping_interval: Optional[float] = None,
        ping_timeout: Optional[float] = None,
    ) -> None:
        """
        Args:
            symbols: List of symbols to subscribe (e.g., ["BTCUSDT", "ETHUSDT"])
            depth_limit: Order book depth (default: from settings.gate_depth_limit)
            want_tickers: Subscribe to spot.tickers (L1)
            want_order_book: Subscribe to spot.order_book (L2)
            ping_interval: Ping interval override (default: from settings)
            ping_timeout: Ping timeout override (default: from settings)
        """
        # Deduplicate symbols
        uniq: List[str] = []
        seen = set()
        for s in symbols:
            u = (s or "").upper().strip()
            if u and u not in seen:
                uniq.append(u)
                seen.add(u)

        self.symbols = uniq
        
        # Use settings with override support
        self.depth_limit = depth_limit if depth_limit is not None else settings.gate_depth_limit
        self.depth_limit = max(5, min(int(self.depth_limit), 50))
        
        self.want_tickers = bool(want_tickers)
        self.want_order_book = bool(want_order_book)

        # WS URL from settings (auto-detects live/testnet)
        self._ws_url = settings.ws_base_url_resolved
        self._stop_evt = asyncio.Event()
        self._conn: Optional[websockets.WebSocketClientProtocol] = None

        # Backoff tracking
        self._attempt = 0

        # Ping/pong settings
        self._ping_interval = float(ping_interval if ping_interval is not None else settings.ws_ping_interval_sec)
        self._ping_timeout = float(ping_timeout if ping_timeout is not None else settings.ws_ping_timeout)
        self._hb_task: Optional[asyncio.Task] = None

        # Recv timeout: ping_interval + ping_timeout + buffer, with multiplier
        base_timeout = self._ping_interval + self._ping_timeout
        self._recv_timeout = max(base_timeout * settings.ws_recv_timeout_multiplier, 45.0)

        logger.info(
            f"Gate WS initialized: url={self._ws_url}, symbols={len(self.symbols)}, "
            f"depth={self.depth_limit}, ping={self._ping_interval}s, recv_timeout={self._recv_timeout:.1f}s"
        )

    # ───────── public ─────────

    async def run(self) -> None:
        """Main loop: connect, stream, reconnect on failure."""
        if not self.symbols:
            logger.info("Gate WS: no symbols to subscribe; exiting.")
            return

        # Enable depth tracking on book_tracker
        if _BOOK_TRACKER is not None:
            for s in self.symbols:
                with suppress(Exception):
                    await _BOOK_TRACKER.enable_depth(s, True)  # type: ignore[attr-defined]

        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
                self._attempt = 0  # Reset on clean exit
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._attempt += 1
                # Exponential backoff using REST retry settings
                base = settings.rest_retry_backoff_ms / 1000.0
                delay = min(
                    settings.rest_backoff_max_sec,
                    base * (settings.rest_retry_backoff_factor ** min(self._attempt, 5))
                )
                logger.warning(
                    f"Gate WS error: {e!r} — reconnect attempt #{self._attempt} in {delay:.1f}s"
                )
                try:
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

        await self._cleanup()

    async def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Gate WS: stop requested")
        self._stop_evt.set()
        await self._cleanup()

    # ───────── internals ─────────

    async def _cleanup(self) -> None:
        """Clean up heartbeat task and websocket connection."""
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
        """Connect to Gate WS and stream messages."""
        url = self._ws_url
        logger.info(
            f"Gate WS connecting → {url} | symbols={self.symbols} | depth_limit={self.depth_limit}"
        )

        async with websockets.connect(
            url,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
            close_timeout=5,
            max_size=4194304,  # 4MB frames (2^22)
        ) as ws:
            self._conn = ws

            # Subscribe to channels
            await self._subscribe(ws)
            
            # Start heartbeat task
            self._hb_task = asyncio.create_task(self._heartbeat(ws))

            # Message loop
            while not self._stop_evt.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=self._recv_timeout)
                except asyncio.TimeoutError:
                    # Quiet period → soft ping (don't reconnect)
                    with suppress(Exception):
                        await ws.ping()
                    continue

                if not msg:
                    continue

                # Parse JSON
                try:
                    data = json.loads(msg)
                except Exception:
                    continue

                # Filter control events
                if isinstance(data, dict) and data.get("event") in {"ping", "pong"}:
                    continue

                await self._handle_message(data)

    async def _heartbeat(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Periodic ping task."""
        try:
            while not self._stop_evt.is_set():
                await asyncio.sleep(self._ping_interval)
                with suppress(Exception):
                    await ws.ping()
        except asyncio.CancelledError:
            return

    async def _subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        """
        Subscribe to Gate.io v4 WS channels:
        • spot.tickers: payload = [<pair>, <pair>, ...]
        • spot.order_book: payload = [<pair>, "<limit>", "100ms"]
        """
        pairs = [_to_gate_pair(s) for s in self.symbols]

        # Subscribe to tickers (L1)
        if self.want_tickers and pairs:
            sub = {
                "time": int(time.time()),
                "channel": "spot.tickers",
                "event": "subscribe",
                "payload": pairs,
            }
            with suppress(Exception):
                await ws.send(json.dumps(sub))
                logger.debug(f"Gate WS: subscribed to spot.tickers for {len(pairs)} pairs")

        # Subscribe to order book (L2)
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
            logger.debug(f"Gate WS: subscribed to spot.order_book for {len(pairs)} pairs (depth={self.depth_limit})")

    async def _handle_message(self, data: Any) -> None:
        """
        Route Gate.io messages to appropriate handlers.
        
        Expected shapes:
        • {"channel": "spot.tickers", "event": "update", "result": {...} | [{...}, ...]}
        • {"channel": "spot.order_book", "event": "update", "result": {...}}
        """
        if not isinstance(data, dict):
            return

        channel = data.get("channel")
        event = data.get("event")
        result = data.get("result") or data.get("payload") or data.get("data")

        # Ignore acks and non-update events
        if event not in {"update", "subscribe"}:
            return

        if channel == "spot.tickers":
            await self._handle_ticker_result(result)
        elif channel == "spot.order_book":
            await self._handle_order_book_result(result)

    # ───────── handlers ─────────

    async def _handle_ticker_result(self, result: Any) -> None:
        """Handle spot.tickers updates (L1 best bid/ask)."""
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

            # L1 often has no sizes; depth handler will populate them
            with suppress(Exception):
                await on_book_ticker(sym, bid, 0.0, ask, 0.0, ts_ms=now_ms)

    async def _handle_order_book_result(self, result: Any) -> None:
        """Handle spot.order_book updates (L2 snapshots)."""
        # Occasionally comes as single-item list
        if isinstance(result, list) and result and isinstance(result[0], dict):
            result = result[0]

        if not isinstance(result, dict):
            return

        pair = str(result.get("currency_pair") or result.get("s") or "").upper()
        if not pair:
            return
        sym = pair.replace("_", "")

        bids_raw = result.get("bids") or []
        asks_raw = result.get("asks") or []

        bids = _parse_levels(bids_raw)
        asks = _parse_levels(asks_raw)
        if not bids and not asks:
            return

        ts_ms = int(result.get("t", 0)) or int(time.time() * 1000)
        with suppress(Exception):
            await on_partial_depth(
                sym,
                bids[: self.depth_limit],
                asks[: self.depth_limit],
                ts_ms=ts_ms
            )


__all__ = ["GateWebSocketClient"]