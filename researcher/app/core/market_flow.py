"""
MarketFlow — real-time tape + order book metrics for researcher.

Two lightweight collectors (MEXC + Gate) subscribe to:
  - trades channel  → buy_pressure, trade_velocity
  - depth channel   → book_imbalance

Metrics are per-symbol, per-exchange, stored in FlowTracker.
SpreadMatrix reads them at spread-computation time and injects into
the spread dict → paper_trader uses them at entry → CSV dataset.

Features produced (all floats, None if no data yet):
  buy_pressure    0.0–1.0   buy_vol / total_vol last 60s (long-side exchange)
  trade_velocity  float     trades per minute last 60s   (long-side exchange)
  book_imbalance  -1.0–1.0  (bid_qty - ask_qty) / total, top-5 levels
  spread_velocity float     spread_pct change per tick   (last 10 ticks)

MEXC futures WS : wss://contract.mexc.com/edge
  sub.deal  → push.deal   (trades)
  sub.depth → push.depth  (order book snapshot)

Gate.io futures WS: wss://fx-ws.gateio.ws/v4/ws/usdt
  futures.trades     → update  (trades)
  futures.order_book → all     (order book snapshot, limit=5)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Optional

import websockets

logger = logging.getLogger(__name__)

_PING_INTERVAL   = 20.0
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT    = _PING_INTERVAL * 3
_TAPE_WINDOW_SEC = 60          # rolling window for tape metrics
_MAX_TRADES      = 500         # max trades kept per symbol/exchange
_BOOK_LEVELS     = 5           # how many levels to use for imbalance


# ── Data containers ────────────────────────────────────────────────────────────

@dataclass
class _TradeTick:
    ts_ms:    int
    size_usd: float
    is_buy:   bool


@dataclass
class _BookSnap:
    bid_qty: float   # sum of top-N bid sizes
    ask_qty: float   # sum of top-N ask sizes


# ── FlowTracker ────────────────────────────────────────────────────────────────

class FlowTracker:
    """
    Stores and computes flow metrics per (symbol, exchange).
    Thread-safe enough for asyncio (single-threaded event loop).
    """

    def __init__(self) -> None:
        # (symbol, exchange) → deque of _TradeTick
        self._tape:  dict[tuple[str, str], deque] = {}
        # (symbol, exchange) → latest _BookSnap
        self._book:  dict[tuple[str, str], _BookSnap] = {}

    # ── ingest ─────────────────────────────────────────────────────────────────

    def on_trade(self, symbol: str, exchange: str,
                 price: float, size: float, is_buy: bool, ts_ms: int) -> None:
        key = (symbol, exchange)
        if key not in self._tape:
            self._tape[key] = deque(maxlen=_MAX_TRADES)
        self._tape[key].append(_TradeTick(
            ts_ms=ts_ms,
            size_usd=price * size,
            is_buy=is_buy,
        ))

    def on_book(self, symbol: str, exchange: str,
                bids: list[tuple[float, float]],
                asks: list[tuple[float, float]]) -> None:
        bid_qty = sum(s for _, s in bids[:_BOOK_LEVELS])
        ask_qty = sum(s for _, s in asks[:_BOOK_LEVELS])
        self._book[(symbol, exchange)] = _BookSnap(bid_qty, ask_qty)

    # ── query ──────────────────────────────────────────────────────────────────

    def buy_pressure(self, symbol: str, exchange: str) -> Optional[float]:
        """buy_volume / total_volume in last 60s. None if no data."""
        trades = self._recent_trades(symbol, exchange)
        if not trades:
            return None
        total = sum(t.size_usd for t in trades)
        if total == 0:
            return None
        buy = sum(t.size_usd for t in trades if t.is_buy)
        return round(buy / total, 4)

    def trade_velocity(self, symbol: str, exchange: str) -> Optional[float]:
        """Trades per minute in last 60s. None if no data."""
        trades = self._recent_trades(symbol, exchange)
        if not trades:
            return None
        return round(len(trades), 2)  # per 60s window = per minute

    def book_imbalance(self, symbol: str, exchange: str) -> Optional[float]:
        """(bid_qty - ask_qty) / total at top-5 levels. +1=all bids, -1=all asks."""
        snap = self._book.get((symbol, exchange))
        if snap is None:
            return None
        total = snap.bid_qty + snap.ask_qty
        if total == 0:
            return None
        return round((snap.bid_qty - snap.ask_qty) / total, 4)

    def get_metrics(self, symbol: str, exchange: str) -> dict:
        return {
            "buy_pressure":   self.buy_pressure(symbol, exchange),
            "trade_velocity": self.trade_velocity(symbol, exchange),
            "book_imbalance": self.book_imbalance(symbol, exchange),
        }

    # ── helpers ────────────────────────────────────────────────────────────────

    def _recent_trades(self, symbol: str, exchange: str) -> list[_TradeTick]:
        key = (symbol, exchange)
        if key not in self._tape:
            return []
        cutoff = int(time.time() * 1000) - _TAPE_WINDOW_SEC * 1000
        return [t for t in self._tape[key] if t.ts_ms >= cutoff]


# ── MEXC Flow Collector ────────────────────────────────────────────────────────

class MexcFlowCollector:
    """
    Subscribes to MEXC futures deals + depth channels.
    Feeds data into a shared FlowTracker.

    Channels per symbol:
      {"method": "sub.deal",  "param": {"symbol": "BTC_USDT"}}
      {"method": "sub.depth", "param": {"symbol": "BTC_USDT", "limit": 5}}
    """

    _WS_URL = "wss://contract.mexc.com/edge"

    def __init__(self, tracker: FlowTracker) -> None:
        self._tracker = tracker
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[MEXC-Flow] Started for %d symbols", len(self._symbols))

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task

    async def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[MEXC-Flow] %r — reconnecting in %.0fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _stream(self) -> None:
        async with websockets.connect(
            self._WS_URL,
            ping_interval=None,
            open_timeout=15,
            close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            for sym in self._symbols:
                await ws.send(json.dumps({"method": "sub.deal",  "param": {"symbol": sym}}))
                await ws.send(json.dumps({"method": "sub.depth", "param": {"symbol": sym, "limit": _BOOK_LEVELS}}))
            logger.info("[MEXC-Flow] Subscribed deals+depth for %d symbols", len(self._symbols))

            hb = asyncio.create_task(self._heartbeat(ws))
            try:
                while not self._stop_evt.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    # MEXC sometimes sends JSON arrays (e.g. batch updates or pong)
                    # Unwrap single-element list; skip multi-element or non-dict
                    if isinstance(msg, list):
                        if len(msg) == 1 and isinstance(msg[0], dict):
                            msg = msg[0]
                        else:
                            continue
                    if not isinstance(msg, dict):
                        continue

                    channel        = msg.get("channel", "")
                    data           = msg.get("data", {})
                    top_sym        = msg.get("symbol", "")  # MEXC puts symbol at top level

                    try:
                        if channel == "push.deal":
                            self._handle_deals(data, top_sym)
                        elif channel == "push.depth":
                            self._handle_depth(data, top_sym)
                    except Exception:
                        pass  # never crash the stream on a bad message
            finally:
                hb.cancel()
                with suppress(Exception):
                    await hb

    def _handle_deals(self, data, fallback_symbol: str = "") -> None:
        """
        MEXC push.deal comes in two formats:
          Format A (old): data = {"symbol": "X", "deals": [{p, v, T, t}, ...]}
          Format B (live): data = [{p, v, T, t}, ...]  +  top-level msg.symbol = "X"
        """
        if isinstance(data, list):
            # Format B — deals list directly, symbol from top-level msg
            symbol = fallback_symbol
            deals  = data
        elif isinstance(data, dict):
            # Format A — nested
            symbol = data.get("symbol", "") or fallback_symbol
            deals  = data.get("deals", [])
        else:
            return

        if not symbol:
            return
        for deal in deals:
            try:
                price  = float(deal["p"])
                size   = float(deal["v"])
                is_buy = int(deal.get("T", 1)) == 1
                ts_ms  = int(deal.get("t", time.time() * 1000))
                self._tracker.on_trade(symbol, "mexc", price, size, is_buy, ts_ms)
            except (KeyError, ValueError, TypeError):
                continue

    def _handle_depth(self, data, fallback_symbol: str = "") -> None:
        """
        MEXC push.depth:
          Format A: data = {"symbol": "X", "bids": [[p,s],...], "asks": [[p,s],...]}
          Format B: data = {"symbol": "X", "bids": [{"p":p,"v":s},...], ...}
          In all formats symbol may also be at top-level msg.
        """
        if not isinstance(data, dict):
            return
        symbol = data.get("symbol", "") or fallback_symbol
        if not symbol:
            return
        try:
            raw_bids = data.get("bids", [])
            raw_asks = data.get("asks", [])
            # Handle both [price, size] lists and {"p":price, "v":size} dicts
            def _parse_levels(levels):
                result = []
                for lvl in levels:
                    if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                        result.append((float(lvl[0]), float(lvl[1])))
                    elif isinstance(lvl, dict):
                        p = lvl.get("p") or lvl.get("price", 0)
                        s = lvl.get("v") or lvl.get("size") or lvl.get("quantity", 0)
                        result.append((float(p), float(s)))
                return result
            bids = _parse_levels(raw_bids)
            asks = _parse_levels(raw_asks)
            self._tracker.on_book(symbol, "mexc", bids, asks)
        except (ValueError, TypeError):
            pass

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                with suppress(Exception):
                    await ws.send(json.dumps({"method": "ping"}))
        except asyncio.CancelledError:
            return


# ── Gate Flow Collector ────────────────────────────────────────────────────────

class GateFlowCollector:
    """
    Subscribes to Gate.io futures trades + order_book channels.
    Feeds data into a shared FlowTracker.

    Channels:
      futures.trades      → update events with trades list
      futures.order_book  → all events with bids/asks snapshot
    """

    _WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"

    def __init__(self, tracker: FlowTracker) -> None:
        self._tracker = tracker
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[Gate-Flow] Started for %d symbols", len(self._symbols))

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task

    async def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[Gate-Flow] %r — reconnecting in %.0fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _stream(self) -> None:
        contracts = [self._to_contract(s) for s in self._symbols]

        async with websockets.connect(
            self._WS_URL,
            ping_interval=None,
            open_timeout=15,
            close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            t = int(time.time())
            await ws.send(json.dumps({
                "time": t, "channel": "futures.trades",
                "event": "subscribe", "payload": contracts,
            }))
            # Subscribe order book per symbol (Gate requires one sub per contract)
            for c in contracts:
                await ws.send(json.dumps({
                    "time": int(time.time()),
                    "channel": "futures.order_book",
                    "event": "subscribe",
                    "payload": [c, "5", "0"],  # contract, limit=5, accuracy=0
                }))
            logger.info("[Gate-Flow] Subscribed trades+book for %d symbols", len(contracts))

            hb = asyncio.create_task(self._heartbeat(ws))
            try:
                while not self._stop_evt.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    channel = msg.get("channel", "")
                    event   = msg.get("event", "")
                    result  = msg.get("result")

                    if not result or event in ("subscribe", "ping", "pong"):
                        continue

                    if channel == "futures.trades":
                        self._handle_trades(result)
                    elif channel == "futures.order_book":
                        self._handle_book(result)
            finally:
                hb.cancel()
                with suppress(Exception):
                    await hb

    def _handle_trades(self, result) -> None:
        items = result if isinstance(result, list) else [result]
        for item in items:
            try:
                contract = item.get("contract", "")
                symbol   = self._contract_to_symbol(contract)
                price    = float(item["price"])
                size     = abs(float(item["size"]))
                is_buy   = float(item["size"]) > 0
                ts_ms    = int(item.get("create_time_ms", time.time() * 1000))
                self._tracker.on_trade(symbol, "gate", price, size, is_buy, ts_ms)
            except (KeyError, ValueError, TypeError):
                continue

    def _handle_book(self, result) -> None:
        try:
            contract = result.get("contract", "")
            symbol   = self._contract_to_symbol(contract)
            bids = [(float(p), float(s)) for p, s in
                    [(b["p"], b["s"]) for b in result.get("bids", [])]]
            asks = [(float(p), float(s)) for p, s in
                    [(a["p"], a["s"]) for a in result.get("asks", [])]]
            self._tracker.on_book(symbol, "gate", bids, asks)
        except (KeyError, ValueError, TypeError):
            pass

    def _to_contract(self, sym: str) -> str:
        s = sym.upper().replace("-", "_")
        if "_" in s:
            return s
        for q in ("USDT", "USD", "BTC", "ETH"):
            if s.endswith(q):
                return f"{s[:-len(q)]}_{q}"
        return s

    def _contract_to_symbol(self, contract: str) -> str:
        # Gate contracts use same format as our internal symbols
        return contract.upper()

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                ping = {"time": int(time.time()), "channel": "futures.ping",
                        "event": "", "payload": []}
                with suppress(Exception):
                    await ws.send(json.dumps(ping))
        except asyncio.CancelledError:
            return
