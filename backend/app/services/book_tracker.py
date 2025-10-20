# app/services/book_tracker.py
from __future__ import annotations

import asyncio
import inspect
import os
import time
import random
from contextlib import suppress
from typing import Any, Dict, List, Optional, Sequence, Tuple, AsyncGenerator, Set, Deque
from collections import deque
from dataclasses import dataclass, asdict

import httpx

from app.config.settings import settings

# ---- constants (guarded import) ---------------------------------
try:
    from app.config.constants import ABSORPTION_X_BPS as DEFAULT_ABS_BPS  # type: ignore
except Exception:
    DEFAULT_ABS_BPS = 5.0  # safe default: ±5 bps

# ----- ScanRow dataclass (temporary; move to scanner.py later) -----
@dataclass
class ScanRow:
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    spread_bps: float = 0.0
    imbalance: float = 0.5
    eff_spread_bps: float = 0.0
    usdpm: float = 0.0
    tpm: float = 0.0
    vol_pattern: float = 0.0  # stability 0–100
    atr_proxy: float = 0.0
    depth_5bps: float = 0.0
    depth_10bps: float = 0.0
    liquidity_grade: str = "C"
    dca_potential: float = 0.0
    score: float = 0.0
    last_update: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

# ----- provider helpers -----
def _provider() -> str:
    try:
        ap = getattr(settings, "active_provider", None)
        if ap:
            return str(ap).upper()
        return (settings.exchange_provider or "").upper()
    except Exception:
        return (os.getenv("ACTIVE_PROVIDER", "") or os.getenv("EXCHANGE_PROVIDER", "") or "").upper()


def _mode() -> str:
    try:
        am = getattr(settings, "active_mode", None)
        if am:
            return str(am).lower()
        return (settings.account_mode or "").lower()
    except Exception:
        return (os.getenv("ACTIVE_MODE", "") or os.getenv("ACCOUNT_MODE", "") or "").lower()


def _is_mexc() -> bool:
    return _provider() == "MEXC"


def _is_binance() -> bool:
    return _provider() == "BINANCE"


def _is_gate() -> bool:
    p = _provider()
    return p in {"GATE", "GATEIO", "GATE.IO", "GATEIO_SPOT"}


def _rest_base_url() -> str:
    """
    Unified, provider/mode-aware REST base.
    Uses settings.rest_base_url_resolved so we don't rely on ad-hoc env combos.
    """
    try:
        base = getattr(settings, "rest_base_url_resolved", None)
        if base:
            return str(base)
    except Exception:
        pass
    # ultimate fallback
    return "https://api.mexc.com"


# ----- Gate symbol mapping -----
def _to_gate_pair(sym: str) -> str:
    s = (sym or "").upper().strip()
    if "_" in s:
        return s
    for q in ("USDT", "USD", "FDUSD", "BUSD", "BTC", "ETH"):
        if s.endswith(q):
            base = s[: -len(q)]
            return f"{base}_{q}"
    if len(s) > 4:
        return f"{s[:-4]}_{s[-4:]}"
    return s


def _from_gate_pair(pair: str) -> str:
    return (pair or "").replace("_", "").upper()


# Try full tracker first; fallback to a minimal one if unavailable.
try:
    from app.market_data.book_tracker import (
        book_tracker,
        on_book_ticker as _on_bt,
        on_partial_depth as _on_depth,
    )
except Exception:
    class _MiniBookTracker:
        def __init__(self) -> None:
            self._lock = asyncio.Lock()
            self._quotes: Dict[str, Dict[str, Any]] = {}
            self._trackers: Dict[str, ScanRow] = {}
            self._price_buffers: Dict[str, Deque[float]] = {}
            self._subscribers: List[asyncio.Queue[Dict[str, Any]]] = []

        async def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
            q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
            async with self._lock:
                self._subscribers.append(q)
            return q

        async def unsubscribe(self, q: asyncio.Queue[Dict[str, Any]]) -> None:
            async with self._lock:
                with suppress(ValueError):
                    self._subscribers.remove(q)

        async def _broadcast(self, evt: Dict[str, Any]) -> None:
            async with self._lock:
                subs = list(self._subscribers)
            for qq in subs:
                with suppress(Exception):
                    qq.put_nowait(dict(evt))

        def _recalc_score(self, row: ScanRow) -> None:
            preset = getattr(settings, 'scan_preset', 'balanced')
            spread_factor = 100 / max(row.spread_bps + 1, 1)
            vol_factor = row.vol_pattern / 100
            usd_factor = min(1.0, row.usdpm / 1000)
            tpm_factor = min(row.tpm / 10, 1.0)
            dca_factor = min(row.dca_potential / 10, 1.0)
            if preset == 'scalper':
                row.score = vol_factor * spread_factor * tpm_factor * 100
            else:
                row.score = vol_factor * spread_factor * dca_factor * usd_factor * 100
            row.score = max(0, min(100, row.score))

        async def update_book_ticker(
            self,
            symbol: str,
            bid: float,
            bid_qty: float,
            ask: float,
            ask_qty: float,
            ts_ms: Optional[int] = None,
        ) -> None:
            sym = (symbol or "").upper()
            ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
            evt: Dict[str, Any] = {
                "symbol": sym,
                "bid": float(bid),
                "bidQty": float(bid_qty),
                "ask": float(ask),
                "askQty": float(ask_qty),
                "ts_ms": int(ts),
            }
            async with self._lock:
                prev = self._quotes.get(sym) or {}
                if "bids" in prev:
                    evt["bids"] = prev["bids"]
                if "asks" in prev:
                    evt["asks"] = prev["asks"]
                self._quotes[sym] = evt

                if sym not in self._trackers:
                    self._trackers[sym] = ScanRow(symbol=sym)
                row = self._trackers[sym]
                row.bid = bid
                row.ask = ask
                row.mid = (bid + ask) * 0.5 if bid > 0 and ask > 0 else (bid or ask or 0.0)
                row.spread_bps = ((ask - bid) / row.mid * 1e4) if row.mid > 0 and bid > 0 and ask > 0 else 0.0
                row.imbalance = (bid_qty / (bid_qty + ask_qty)) if (bid_qty > 0 or ask_qty > 0) else 0.5
                row.eff_spread_bps = row.spread_bps * (0.5 + abs(row.imbalance - 0.5))
                row.last_update = ts

                # Price buffer for vol_pattern proxy
                if sym not in self._price_buffers:
                    self._price_buffers[sym] = deque(maxlen=60)
                self._price_buffers[sym].append(row.mid)

                buffer = self._price_buffers[sym]
                if len(buffer) >= 2:
                    n = len(buffer)
                    mean_p = sum(buffer) / n
                    var = sum((x - mean_p) ** 2 for x in buffer) / n
                    std_p = var ** 0.5
                    rel_vol = (std_p / mean_p * 1e4) if mean_p > 0 else 0.0
                    row.vol_pattern = max(0.0, min(100.0, 100 - rel_vol * 10))
                    row.atr_proxy = max(buffer) - min(buffer)
                else:
                    row.vol_pattern = 100.0
                    row.atr_proxy = 0.0

                row.dca_potential = row.depth_5bps / max(row.usdpm, 1.0)
                self._recalc_score(row)

            await self._broadcast(evt)

            # SSE emit
            if sym in self._trackers:
                try:
                    sse_pub = _get_sse_publisher()
                    if sse_pub:
                        await sse_pub.emit('scan_row_update', {'symbol': sym, **self._trackers[sym].as_dict()})
                except Exception:
                    pass

        async def update_tape_metrics(
            self,
            symbol: str,
            usdpm: float,
            tpm: float,
            ts_ms: Optional[int] = None,
        ) -> None:
            sym = (symbol or "").upper()
            ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
            async with self._lock:
                if sym not in self._trackers:
                    return
                row = self._trackers[sym]
                row.usdpm = float(usdpm)
                row.tpm = float(tpm)
                row.last_update = ts
                row.dca_potential = row.depth_5bps / max(row.usdpm, 1.0)
                self._recalc_score(row)

            # SSE emit
            try:
                sse_pub = _get_sse_publisher()
                if sse_pub:
                    await sse_pub.emit('scan_row_update', {'symbol': sym, **self._trackers[sym].as_dict()})
            except Exception:
                pass

        async def update_partial_depth(
            self,
            symbol: str,
            bids: Sequence[Tuple[float, float]],
            asks: Sequence[Tuple[float, float]],
            ts_ms: Optional[int] = None,
            keep_levels: int = 10,
        ) -> None:
            sym = (symbol or "").upper()
            ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
            nbids = sorted(
                [(float(p), float(q)) for p, q in bids if (p > 0 and q > 0)],
                key=lambda x: x[0],
                reverse=True,
            )[:keep_levels]
            nasks = sorted(
                [(float(p), float(q)) for p, q in asks if (p > 0 and q > 0)],
                key=lambda x: x[0],
            )[:keep_levels]
            async with self._lock:
                entry = dict(self._quotes.get(sym) or {})
                entry["bids"] = nbids
                entry["asks"] = nasks
                if "bid" not in entry and nbids:
                    entry["bid"], entry["bidQty"] = nbids[0]
                if "ask" not in entry and nasks:
                    entry["ask"], entry["askQty"] = nasks[0]
                entry["ts_ms"] = int(ts)
                self._quotes[sym] = entry

                if sym in self._trackers:
                    row = self._trackers[sym]
                    mid = row.mid
                    if mid <= 0 and "bid" in entry and "ask" in entry:
                        mid = (entry["bid"] + entry["ask"]) * 0.5
                        row.mid = mid

                    for bps in [5, 10]:
                        band = bps / 10000.0
                        bid_floor = mid * (1.0 - band)
                        ask_cap = mid * (1.0 + band)
                        d_bid = sum(p * q for p, q in nbids if p >= bid_floor)
                        d_ask = sum(p * q for p, q in nasks if p <= ask_cap)
                        depth = d_bid + d_ask
                        if bps == 5:
                            row.depth_5bps = depth
                        else:
                            row.depth_10bps = depth

                    # Liquidity grade
                    if row.depth_5bps > 5000:
                        row.liquidity_grade = "A"
                    elif row.depth_5bps > 1000:
                        row.liquidity_grade = "B"
                    elif row.depth_5bps > 100:
                        row.liquidity_grade = "C"
                    else:
                        row.liquidity_grade = "D"

                    row.dca_potential = row.depth_5bps / max(row.usdpm, 1.0)
                    self._recalc_score(row)

            await self._broadcast({"symbol": sym, **self._quotes[sym]})

            # SSE emit
            if sym in self._trackers:
                try:
                    sse_pub = _get_sse_publisher()
                    if sse_pub:
                        await sse_pub.emit('scan_row_update', {'symbol': sym, **self._trackers[sym].as_dict()})
                except Exception:
                    pass

        async def get_quote(self, symbol: str) -> Dict[str, Any]:
            async with self._lock:
                return dict(self._quotes.get((symbol or "").upper(), {}))

        async def get_quotes(self, symbols: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
            async with self._lock:
                if not symbols:
                    return [dict(v) for v in self._quotes.values()]
                sel, seen = [], set()
                for s in symbols:
                    sym = (s or "").upper()
                    if sym and sym not in seen:
                        seen.add(sym)
                        sel.append(sym)
                return [dict(v) for k, v in self._quotes.items() if k in sel]

        # --- FIX: provide a sync reset (no 'async with' inside) ---
        def reset(self) -> None:
            """
            Synchronous reset used by provider-switch hooks.
            We avoid 'async with' here to keep this callable from sync code.
            Replacing the dict is atomic in CPython and good enough for our usage.
            """
            self._quotes = {}
            self._trackers = {}
            self._price_buffers = {}

        # Optional async variant if you ever need a locked reset in async code
        async def areset(self) -> None:
            async with self._lock:
                self._quotes.clear()
                self._trackers.clear()
                self._price_buffers.clear()

    book_tracker = _MiniBookTracker()  # type: ignore

    async def _on_bt(
        symbol: str,
        bid: float,
        bid_qty: float,
        ask: float,
        ask_qty: float,
        ts_ms: Optional[int],
    ) -> None:
        await book_tracker.update_book_ticker(symbol, bid, bid_qty, ask, ask_qty, ts_ms=ts_ms)

    async def _on_depth(
        symbol: str,
        bids: Sequence[Tuple[float, float]],
        asks: Sequence[Tuple[float, float]],
        ts_ms: Optional[int],
    ) -> None:
        await book_tracker.update_partial_depth(symbol, bids, asks, ts_ms=ts_ms, keep_levels=10)

    async def on_tape_metrics(
        symbol: str,
        usdpm: float,
        tpm: float,
        ts_ms: Optional[int] = None,
    ) -> None:
        await book_tracker.update_tape_metrics(symbol, usdpm, tpm, ts_ms=ts_ms)


# SSE publisher helper
def _get_sse_publisher():
    try:
        from app.services.sse_publisher import sse_publisher
        return sse_publisher
    except Exception:
        return None


# ── Public API ───────────────────────────────────────────────────────────────
def _with_derived(q: Dict[str, Any]) -> Dict[str, Any]:
    if not q:
        return {}
    sym = (q.get("symbol") or "").upper()
    bid = float(q.get("bid") or 0.0)
    ask = float(q.get("ask") or 0.0)
    bid_qty = float(q.get("bidQty") or q.get("bid_qty") or 0.0)
    ask_qty = float(q.get("askQty") or q.get("ask_qty") or 0.0)
    bids_l2 = q.get("bids") or []
    asks_l2 = q.get("asks") or []

    # Fallback sizes from L2 best if ticker sizes are missing
    if (not bid_qty or bid_qty <= 0.0) and bids_l2:
        with suppress(Exception):
            bid_qty = float(bids_l2[0][1])
    if (not ask_qty or ask_qty <= 0.0) and asks_l2:
        with suppress(Exception):
            ask_qty = float(asks_l2[0][1])

    ts_raw = q.get("ts_ms") or q.get("ts") or 0
    try:
        ts_i = int(ts_raw)
    except Exception:
        ts_i = 0
    ts_ms = ts_i if ts_i > 0 and (bid > 0.0 or ask > 0.0) else (int(time.time() * 1000) if (bid > 0.0 or ask > 0.0) else 0)

    mid = (bid + ask) * 0.5 if (bid > 0.0 and ask > 0.0) else (bid or ask or 0.0)
    spread = (ask - bid) if (ask > 0.0 and bid > 0.0) else 0.0
    spread_bps = (spread / mid * 1e4) if mid > 0 else 0.0

    out: Dict[str, Any] = {
        "symbol": sym,
        "bid": bid,
        "ask": ask,
        "bidQty": bid_qty,
        "askQty": ask_qty,
        "ts_ms": ts_ms,
        "mid": mid,
        "spread": spread,
        "spread_bps": spread_bps,
    }
    if bids_l2:
        out["bids"] = bids_l2
    if asks_l2:
        out["asks"] = asks_l2

    # Imbalance
    try:
        bq = float(out.get("bidQty", 0.0))
        aq = float(out.get("askQty", 0.0))
        out["imbalance"] = (bq / (bq + aq)) if (bq > 0.0 or aq > 0.0) else 0.5
    except Exception:
        out["imbalance"] = 0.5

    # Absorption over ±X bps band (USD)
    try:
        x_bps = float(getattr(settings, "absorption_x_bps", DEFAULT_ABS_BPS) or DEFAULT_ABS_BPS)
    except Exception:
        x_bps = float(DEFAULT_ABS_BPS)
    if mid > 0 and (bids_l2 or asks_l2) and x_bps > 0:
        band = x_bps / 1e4
        bid_floor = mid * (1.0 - band)
        ask_cap = mid * (1.0 + band)

        def _sum_usd(levels, side: str) -> float:
            total = 0.0
            if side == "bid":
                for p, q in levels:
                    with suppress(Exception):
                        pf, qf = float(p), float(q)
                        if pf > 0 and qf > 0 and pf >= bid_floor:
                            total += pf * qf
                        elif pf < bid_floor:
                            break
            else:
                for p, q in levels:
                    with suppress(Exception):
                        pf, qf = float(p), float(q)
                        if pf > 0 and qf > 0 and pf <= ask_cap:
                            total += pf * qf
                        elif pf > ask_cap:
                            break
            return total

        out["absorption_bid_usd"] = _sum_usd(bids_l2, "bid") if bids_l2 else 0.0
        out["absorption_ask_usd"] = _sum_usd(asks_l2, "ask") if asks_l2 else 0.0
    else:
        out["absorption_bid_usd"] = 0.0
        out["absorption_ask_usd"] = 0.0

    return out


async def on_book_ticker(
    symbol: str, bid: float, bid_qty: float, ask: float, ask_qty: float, ts_ms: Optional[int] = None
) -> None:
    await _on_bt(symbol, bid, bid_qty, ask, ask_qty, ts_ms)


async def on_partial_depth(
    symbol: str, bids: Sequence[Tuple[float, float]], asks: Sequence[Tuple[float, float]], ts_ms: Optional[int] = None
) -> None:
    await _on_depth(symbol, bids, asks, ts_ms)


async def get_quote(symbol: str) -> Dict[str, Any]:
    sym = (symbol or "").upper()
    
    # 🔥 PRIORITY 1: Try price_poller first (our new primary source)
    try:
        from app.services.price_poller import get_poller
        poller = get_poller()
        price_data = poller.get_price(sym)
        
        if price_data:
            bid = price_data.get("bid", 0.0)
            ask = price_data.get("ask", 0.0)
            mid = price_data.get("mid", 0.0)
            
            # Build minimal quote
            quote = {
                "symbol": sym,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "bidQty": 0.0,  # Poller doesn't have sizes
                "askQty": 0.0,
                "ts_ms": int(price_data.get("timestamp", 0) * 1000),
            }
            
            # Try to get depth/imbalance from cache (if available)
            try:
                raw = await book_tracker.get_quote(sym)
                if raw:
                    # Merge depth data from cache
                    if "bids" in raw:
                        quote["bids"] = raw["bids"]
                    if "asks" in raw:
                        quote["asks"] = raw["asks"]
                    if "bidQty" in raw and raw["bidQty"] > 0:
                        quote["bidQty"] = raw["bidQty"]
                    if "askQty" in raw and raw["askQty"] > 0:
                        quote["askQty"] = raw["askQty"]
            except:
                pass
            
            # Apply _with_derived to add spread_bps, imbalance, absorption
            return _with_derived(quote)
            
    except Exception as e:
        # Silently fall back to cache
        pass
    
    # 🔥 FALLBACK: Use internal cache (legacy/backup)
    raw = await book_tracker.get_quote(symbol)
    return _with_derived(raw) if raw else {}


async def get_all_quotes(symbols: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    raws = await book_tracker.get_quotes(symbols)
    out, seen = [], set()
    for q in raws:
        sym = (q.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        qd = _with_derived(q)
        if (qd.get("bid", 0.0) > 0.0) or (qd.get("ask", 0.0) > 0.0):
            out.append(qd)
            seen.add(sym)
    return out


# ── Coalesced streaming for SSE ─────────────────────────────────────────────
async def stream_quote_batches(symbols: Sequence[str], interval_ms: int = 500) -> AsyncGenerator[List[Dict[str, Any]], None]:
    """
    Yield latest quotes every interval_ms, normalized with _with_derived.
    Ensures ingestion for requested symbols even if settings.symbols is empty.
    """
    # Normalize/unique symbols once
    want: List[str] = []
    seen_syms: Set[str] = set()
    for s in symbols:
        sym = (s or "").upper()
        if sym and sym not in seen_syms:
            seen_syms.add(sym)
            want.append(sym)

    # Ensure ingestion (WS or REST)
    if want:
        with suppress(Exception):
            await ensure_symbols_subscribed(want)

    queue: asyncio.Queue[Dict[str, Any]] = await book_tracker.subscribe()
    try:
        latest: Dict[str, Dict[str, Any]] = {}

        async def drain_once(timeout: float) -> None:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                sym = (evt.get("symbol") or "").upper()
                if not sym or (want and sym not in want):
                    continue
                latest[sym] = evt  # keep most recent per symbol

        # период + небольшой джиттер, чтобы подписчики не синхронизировались
        period = max(0.05, min(interval_ms / 1000.0, 60.0)) + random.uniform(0.0, 0.02)

        rest_base = _rest_base_url()
        async with httpx.AsyncClient(
            base_url=rest_base, headers={"Accept": "application/json"}, timeout=8.0
        ) as depth_client:
            while True:
                await drain_once(period)

                if not latest and want:
                    batch = await get_all_quotes(want)
                else:
                    batch = [
                        q
                        for q in (_with_derived(v) for v in latest.values())
                        if (q.get("bid", 0.0) > 0.0) or (q.get("ask", 0.0) > 0.0)
                    ]
                    latest.clear()

                if not batch:
                    continue

                # Attach L2 from cache if present
                need_l2 = [q["symbol"] for q in batch if not q.get("bids") or not q.get("asks")]
                if need_l2:
                    with suppress(Exception):
                        raws = await book_tracker.get_quotes(need_l2)  # type: ignore[attr-defined]
                        by_sym = {(r.get("symbol") or "").upper(): r for r in raws}
                        for q in batch:
                            raw = by_sym.get(q["symbol"])
                            if not raw:
                                continue
                            if raw.get("bids") and not q.get("bids"):
                                q["bids"] = raw["bids"]
                            if raw.get("asks") and not q.get("asks"):
                                q["asks"] = raw["asks"]

                # Fill remaining L2 via REST (best effort)
                still_missing = [q["symbol"] for q in batch if not q.get("bids") or not q.get("asks")]
                if still_missing:
                    with suppress(Exception):
                        tasks = [asyncio.create_task(_fetch_depth_generic(depth_client, s, limit=10)) for s in still_missing]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        for res in results:
                            if isinstance(res, Exception) or res is None:
                                continue
                            sym = (res.get("symbol") or "").upper()
                            with suppress(Exception):
                                await _on_depth(sym, res.get("bids", []), res.get("asks", []), ts_ms=res.get("ts_ms"))
                            for q in batch:
                                if q["symbol"] == sym:
                                    if res.get("bids"):
                                        q["bids"] = res["bids"]
                                    if res.get("asks"):
                                        q["asks"] = res["asks"]

                if batch:
                    yield batch
    finally:
        await book_tracker.unsubscribe(queue)


# ── WS manager + REST fallback ──────────────────────────────────────────────
try:
    from app.market_data.ws_client import (
        MEXCWebSocketClient as _WSClient,
        PROTO_AVAILABLE as _WS_PROTO_OK,
    )
except Exception:
    _WSClient = None  # type: ignore[assignment]
    _WS_PROTO_OK = False

_REST_POLL_TASK: Optional[asyncio.Task[None]] = None
_SUBSCRIBED: Set[str] = set()

_WS_TASK: Optional[asyncio.Task[None]] = None
_WS_CLIENT: Optional[Any] = None
_WS_WANTED: Set[str] = set()
_WS_RUNNING: Set[str] = set()

_DEPTH_TASK: Optional[asyncio.Task[None]] = None
_DEPTH_SUBSCRIBED: Set[str] = set()


async def _fetch_ticker_binance_mexc(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get("/api/v3/ticker/bookTicker", params={"symbol": symbol}, timeout=5.0)
        if r.status_code != 200:
            return None
        j = r.json()
        bid = float(j.get("bidPrice") or 0.0)
        ask = float(j.get("askPrice") or 0.0)
        bid_qty = float(j.get("bidQty") or 0.0)
        ask_qty = float(j.get("askQty") or 0.0)
        ts_ms = int(time.time() * 1000)
        return {"symbol": symbol, "bid": bid, "ask": ask, "bidQty": bid_qty, "askQty": ask_qty, "ts_ms": ts_ms}
    except Exception:
        return None


async def _fetch_depth_binance_mexc(client: httpx.AsyncClient, symbol: str, limit: int = 10) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get("/api/v3/depth", params={"symbol": symbol, "limit": limit}, timeout=5.0)
        if r.status_code != 200:
            return None
        j = r.json()
        bids_raw = j.get("bids") or []
        asks_raw = j.get("asks") or []
        bids: List[Tuple[float, float]] = []
        asks: List[Tuple[float, float]] = []
        for it in bids_raw:
            with suppress(Exception):
                p = float(it[0]); q = float(it[1])
                if p > 0 and q > 0:
                    bids.append((p, q))
        for it in asks_raw:
            with suppress(Exception):
                p = float(it[0]); q = float(it[1])
                if p > 0 and q > 0:
                    asks.append((p, q))
        ts_ms = int(time.time() * 1000)
        return {"symbol": symbol, "bids": bids, "asks": asks, "ts_ms": ts_ms}
    except Exception:
        return None


async def _fetch_ticker_gate(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    try:
        pair = _to_gate_pair(symbol)
        r = await client.get("/spot/tickers", params={"currency_pair": pair}, timeout=6.0)
        if r.status_code != 200:
            return None
        j = r.json()
        if isinstance(j, list) and j:
            j = j[0]
        bid = float(j.get("highest_bid") or 0.0)
        ask = float(j.get("lowest_ask") or 0.0)
        ts_ms = int(time.time() * 1000)
        return {"symbol": symbol, "bid": bid, "ask": ask, "bidQty": 0.0, "askQty": 0.0, "ts_ms": ts_ms}
    except Exception:
        return None


async def _fetch_depth_gate(client: httpx.AsyncClient, symbol: str, limit: int = 10) -> Optional[Dict[str, Any]]:
    try:
        pair = _to_gate_pair(symbol)
        r = await client.get("/spot/order_book", params={"currency_pair": pair, "limit": limit}, timeout=6.0)
        if r.status_code != 200:
            return None
        j = r.json()
        bids_raw = j.get("bids") or []
        asks_raw = j.get("asks") or []
        bids: List[Tuple[float, float]] = []
        asks: List[Tuple[float, float]] = []
        for it in bids_raw:
            with suppress(Exception):
                p = float(it[0]); q = float(it[1])
                if p > 0 and q > 0:
                    bids.append((p, q))
        for it in asks_raw:
            with suppress(Exception):
                p = float(it[0]); q = float(it[1])
                if p > 0 and q > 0:
                    asks.append((p, q))
        ts_ms = int(time.time() * 1000)
        return {"symbol": symbol, "bids": bids, "asks": asks, "ts_ms": ts_ms}
    except Exception:
        return None


async def _fetch_ticker_generic(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    if _is_gate():
        return await _fetch_ticker_gate(client, symbol)
    return await _fetch_ticker_binance_mexc(client, symbol)


async def _fetch_depth_generic(client: httpx.AsyncClient, symbol: str, limit: int = 10) -> Optional[Dict[str, Any]]:
    if _is_gate():
        return await _fetch_depth_gate(client, symbol, limit=limit)
    return await _fetch_depth_binance_mexc(client, symbol, limit=limit)


async def _rest_poller_loop() -> None:
    base = _rest_base_url()
    depth_min_period_s = 0.8
    last_depth_at: Dict[str, float] = {}
    try:
        async with httpx.AsyncClient(base_url=base, headers={"Accept": "application/json"}, timeout=8.0) as client:
            while True:
                syms = list(_SUBSCRIBED)
                if not syms:
                    await asyncio.sleep(0.5)
                    continue

                ticker_tasks = [asyncio.create_task(_fetch_ticker_generic(client, s)) for s in syms]
                ticker_res = await asyncio.gather(*ticker_tasks, return_exceptions=True)

                now_ms = int(time.time() * 1000)
                for res in ticker_res:
                    if isinstance(res, Exception) or res is None:
                        continue
                    tick = res
                    await _on_bt(
                        tick["symbol"],
                        tick.get("bid", 0.0),
                        tick.get("bidQty", 0.0),
                        tick.get("ask", 0.0),
                        tick.get("askQty", 0.0),
                        ts_ms=tick.get("ts_ms", now_ms),
                    )

                depth_tasks: List[asyncio.Task[Optional[Dict[str, Any]]]] = []
                now = time.monotonic()
                for s in syms:
                    if now - last_depth_at.get(s, 0.0) >= depth_min_period_s:
                        last_depth_at[s] = now
                        depth_tasks.append(asyncio.create_task(_fetch_depth_generic(client, s, limit=10)))

                if depth_tasks:
                    depth_res = await asyncio.gather(*depth_tasks, return_exceptions=True)
                    for res in depth_res:
                        if isinstance(res, Exception) or res is None:
                            continue
                        d = res
                        await _on_depth(d["symbol"], d.get("bids", []), d.get("asks", []), ts_ms=d.get("ts_ms"))

                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        return
    except Exception:
        await asyncio.sleep(1.0)


async def _depth_refresher_loop() -> None:
    base = _rest_base_url()
    min_period_s = 0.9
    last_at: Dict[str, float] = {}
    try:
        async with httpx.AsyncClient(base_url=base, headers={"Accept": "application/json"}, timeout=8.0) as client:
            while True:
                syms = list(_DEPTH_SUBSCRIBED)
                if not syms:
                    await asyncio.sleep(0.4)
                    continue
                now = time.monotonic()
                tasks: List[asyncio.Task[Optional[Dict[str, Any]]]] = []
                for s in syms:
                    if now - last_at.get(s, 0.0) >= min_period_s:
                        last_at[s] = now
                        tasks.append(asyncio.create_task(_fetch_depth_generic(client, s, limit=10)))
                if tasks:
                    res = await asyncio.gather(*tasks, return_exceptions=True)
                    for r in res:
                        if isinstance(r, Exception) or r is None:
                            continue
                        await _on_depth(r["symbol"], r.get("bids", []), r.get("asks", []), ts_ms=r.get("ts_ms"))
                await asyncio.sleep(0.25)
    except asyncio.CancelledError:
        return
    except Exception:
        await asyncio.sleep(1.0)


async def _rest_seed_symbols(symbols: Sequence[str]) -> None:
    base = _rest_base_url()
    try:
        async with httpx.AsyncClient(base_url=base, headers={"Accept": "application/json"}, timeout=8.0) as client:
            t_tasks = [asyncio.create_task(_fetch_ticker_generic(client, s)) for s in symbols]
            ticks = await asyncio.gather(*t_tasks, return_exceptions=True)
            now_ms = int(time.time() * 1000)
            for res in ticks:
                if isinstance(res, Exception) or res is None:
                    continue
                tick = res
                await _on_bt(
                    tick["symbol"],
                    tick.get("bid", 0.0),
                    tick.get("bidQty", 0.0),
                    tick.get("ask", 0.0),
                    tick.get("askQty", 0.0),
                    ts_ms=tick.get("ts_ms", now_ms),
                )
            d_tasks = [asyncio.create_task(_fetch_depth_generic(client, s, limit=10)) for s in symbols]
            deps = await asyncio.gather(*d_tasks, return_exceptions=True)
            for res in deps:
                if isinstance(res, Exception) or res is None:
                    continue
                d = res
                await _on_depth(d["symbol"], d.get("bids", []), d.get("asks", []), ts_ms=d.get("ts_ms"))
    except Exception:
        pass


async def _stop_rest_poller() -> None:
    global _REST_POLL_TASK
    if _REST_POLL_TASK and not _REST_POLL_TASK.done():
        _REST_POLL_TASK.cancel()
        with suppress(Exception):
            await _REST_POLL_TASK
    _REST_POLL_TASK = None


async def _start_rest_poller() -> None:
    global _REST_POLL_TASK
    if _REST_POLL_TASK is None or _REST_POLL_TASK.done():
        _REST_POLL_TASK = asyncio.create_task(_rest_poller_loop())


async def _stop_depth_refresher() -> None:
    global _DEPTH_TASK, _DEPTH_SUBSCRIBED
    if _DEPTH_TASK and not _DEPTH_TASK.done():
        _DEPTH_TASK.cancel()
        with suppress(Exception):
            await _DEPTH_TASK
    _DEPTH_TASK = None
    _DEPTH_SUBSCRIBED.clear()


async def _start_depth_refresher() -> None:
    global _DEPTH_TASK
    if _DEPTH_TASK is None or _DEPTH_TASK.done():
        _DEPTH_TASK = asyncio.create_task(_depth_refresher_loop())


async def _stop_ws() -> None:
    global _WS_TASK, _WS_CLIENT, _WS_RUNNING
    if _WS_TASK and not _WS_TASK.done():
        _WS_TASK.cancel()
        with suppress(Exception):
            await _WS_TASK
    _WS_TASK = None
    _WS_CLIENT = None
    _WS_RUNNING = set()


async def _start_ws(symbols: Set[str]) -> None:
    global _WS_TASK, _WS_CLIENT, _WS_RUNNING
    if not _is_mexc():
        return
    if not _WSClient or not _WS_PROTO_OK:
        return
    await _stop_ws()
    _WS_CLIENT = _WSClient(sorted(symbols), channels=["BOOK_TICKER", "DEPTH_LIMIT"])  # type: ignore[call-arg]
    _WS_TASK = asyncio.create_task(_WS_CLIENT.run())  # type: ignore[arg-type]
    _WS_RUNNING = set(symbols)


async def ensure_symbols_subscribed(symbols: Sequence[str]) -> None:
    norm: Set[str] = set((s or "").upper() for s in symbols if (s or "").strip())
    if not norm:
        return
    if _is_mexc() and _WSClient and _WS_PROTO_OK:
        _WS_WANTED.update(norm)
        if (_WS_TASK is None or _WS_TASK.done()) or (
            not _WS_WANTED.issubset(_WS_RUNNING) or not _WS_RUNNING.issubset(_WS_WANTED)
        ):
            await _start_ws(_WS_WANTED)
        await _rest_seed_symbols(list(norm))
        await _stop_rest_poller()
        _DEPTH_SUBSCRIBED.update(norm)
        await _start_depth_refresher()
        return
    _SUBSCRIBED.update(norm)
    await _start_rest_poller()
    await _rest_seed_symbols(list(norm))
    await _stop_depth_refresher()


# ── reset hook for provider switching ────────────────────────────────────────
def reset() -> None:
    """Reset all trackers/caches for provider switch (hook from config_manager)."""
    global _SUBSCRIBED, _WS_WANTED, _WS_RUNNING, _DEPTH_SUBSCRIBED
    _SUBSCRIBED.clear()
    _WS_WANTED.clear()
    _WS_RUNNING.clear()
    _DEPTH_SUBSCRIBED.clear()

    # Try book_tracker.reset() (supports both sync and async)
    try:
        reset_fn = getattr(book_tracker, "reset", None)
        if callable(reset_fn):
            res = reset_fn()
            if inspect.isawaitable(res):  # if some impl provides async reset()
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(res)  # no running loop (rare in FastAPI context)
                else:
                    loop.create_task(res)  # fire-and-forget
            print("Book tracker reset() called.")
            return
    except Exception as e:
        print(f"Book tracker reset failed: {e}")

    # Fallback: try async variant name if provided
    try:
        areset_fn = getattr(book_tracker, "areset", None)
        if callable(areset_fn):
            coro = areset_fn()
            if inspect.isawaitable(coro):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(coro)
                else:
                    loop.create_task(coro)
            print("Book tracker areset() scheduled.")
            return
    except Exception as e:
        print(f"Book tracker areset failed: {e}")

    print("Reset completed with basic state clears.")