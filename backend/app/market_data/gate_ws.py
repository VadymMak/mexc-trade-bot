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
    from app.market_data.book_tracker import book_tracker as _BOOK_TRACKER  # has enable_depth()
except Exception:  # fallback: no-op if module/attr missing
    _BOOK_TRACKER = None  # type: ignore

logger = logging.getLogger(__name__)


# ───────────────────────── helpers ─────────────────────────

def _is_demo_mode() -> bool:
    """
    Legacy fallback if GATE_WS_ENV is not set.
    """
    try:
        m = getattr(settings, "active_mode", None) or getattr(settings, "account_mode", None) or "paper"
        return str(m).lower() in {"paper", "demo", "test", "testnet"}
    except Exception:
        return True


def _ws_base_url() -> str:
    """
    Resolve Gate WS base with per-exchange precedence:

    1) If settings.gate_ws_env in {LIVE, TESTNET} → choose the corresponding Gate base.
    2) Else use Gate-specific bases with legacy demo/live detection.
    3) As a final fallback, return well-known public endpoints.

    NOTE: We intentionally IGNORE any global ws_base_url_resolved here so Gate can
    run live/testnet independently of ACTIVE_MODE and other providers.
    """
    # 1) Explicit per-exchange env
    env = (getattr(settings, "gate_ws_env", "") or "").strip().upper()
    if env in {"LIVE", "PROD"}:
        base = getattr(settings, "gate_ws_base", None)
        if base:
            logger.info(f"Gate WS selecting LIVE via GATE_WS_ENV → {base}")
            return base
        return "wss://api.gateio.ws/ws/v4/"
    if env in {"TESTNET", "SANDBOX"}:
        base = getattr(settings, "gate_testnet_ws_base", None)
        if base:
            logger.info(f"Gate WS selecting TESTNET via GATE_WS_ENV → {base}")
            return base
        # common testnet endpoint
        return "wss://ws-testnet.gateio.ws/v4/ws/spot"

    # 2) Legacy behavior: derive from active/demo mode
    if _is_demo_mode():
        base = getattr(settings, "gate_testnet_ws_base", None)
        return base or "wss://ws-testnet.gateio.ws/v4/ws/spot"
    base = getattr(settings, "gate_ws_base", None)
    return base or "wss://api.gateio.ws/ws/v4/"


def _to_gate_pair(sym: str) -> str:
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


# ───────────────────────── client ─────────────────────────

class GateWebSocketClient:
    """
    Public Gate Spot WS client:
      • Subscribes to `spot.tickers` (L1) and `spot.order_book` (L2 snapshots).
      • Normalizes events and forwards them to book_tracker.
      • Reconnects with backoff; graceful stop().
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

        # pings
        self._ping_interval = float(ping_interval)
        self._ping_timeout = float(ping_timeout)
        self._hb_task: Optional[asyncio.Task] = None

        # recv timeout: after this we soft-ping and keep the connection
        self._recv_timeout = max(self._ping_interval + self._ping_timeout + 5.0, 45.0)

    # ───────── public ─────────

    async def run(self) -> None:
        if not self.symbols:
            logger.info("Gate WS: no symbols to subscribe; exiting.")
            return

        # Ensure the real BookTracker will actually accept depth updates (no-op if unavailable)
        if _BOOK_TRACKER is not None:
            for s in self.symbols:
                with suppress(Exception):
                    await _BOOK_TRACKER.enable_depth(s, True)  # type: ignore[attr-defined]

        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
                self._attempt = 0  # clean close/reset
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
        self._stop_evt.set()
        await self._cleanup()

    # ───────── internals ─────────

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
            max_size=2**22,                      # ~4MB frames
        ) as ws:
            self._conn = ws

            await self._subscribe(ws)
            self._hb_task = asyncio.create_task(self._heartbeat(ws))

            while not self._stop_evt.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=self._recv_timeout)
                except asyncio.TimeoutError:
                    # quiet period → soft keepalive ping; do not reconnect
                    with suppress(Exception):
                        await ws.ping()
                    continue

                if not msg:
                    continue

                try:
                    data = json.loads(msg)
                except Exception:
                    continue

                # Filter trivial control events
                if isinstance(data, dict) and data.get("event") in {"ping", "pong"}:
                    continue

                await self._handle_message(data)

    async def _heartbeat(self, ws: websockets.WebSocketClientProtocol) -> None:
        try:
            while not self._stop_evt.is_set():
                await asyncio.sleep(self._ping_interval)
                with suppress(Exception):
                    await ws.ping()
        except asyncio.CancelledError:
            return

    async def _subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        """
        Gate v4 WS:
          • spot.tickers    : payload = [<pair>, <pair>, ...]
          • spot.order_book : payload = [<pair>, "<limit>", "100ms"]
        """
        pairs = [_to_gate_pair(s) for s in self.symbols]

        if self.want_tickers and pairs:
            sub = {
                "time": int(time.time()),
                "channel": "spot.tickers",
                "event": "subscribe",
                "payload": pairs,
            }
            with suppress(Exception):
                await ws.send(json.dumps(sub))

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
          {"time":..., "channel":"spot.tickers",    "event":"update", "result": {...} | [ {...}, ... ]}
          {"time":..., "channel":"spot.order_book", "event":"update", "result": {...}}
        """
        if not isinstance(data, dict):
            return

        channel = data.get("channel")
        event = data.get("event")
        result = data.get("result") or data.get("payload") or data.get("data")

        # ignore acks etc.
        if event not in {"update", "subscribe"}:
            return

        if channel == "spot.tickers":
            await self._handle_ticker_result(result)
        elif channel == "spot.order_book":
            await self._handle_order_book_result(result)

    # ───────── handlers ─────────

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

            # L1 often has no sizes; depth handler will populate them later
            with suppress(Exception):
                await on_book_ticker(sym, bid, 0.0, ask, 0.0, ts_ms=now_ms)

    async def _handle_order_book_result(self, result: Any) -> None:
        # Occasionally comes as a single-item list
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
            await on_partial_depth(sym, bids[: self.depth_limit], asks[: self.depth_limit], ts_ms=ts_ms)


__all__ = ["GateWebSocketClient"]
