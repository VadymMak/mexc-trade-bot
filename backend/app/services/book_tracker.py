# app/services/book_tracker.py
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, AsyncGenerator, Set

# Try full tracker first; fallback to a minimal one if unavailable.
try:
    from app.market_data.book_tracker import (
        book_tracker,            # singleton BookTracker (subscribe/unsubscribe, etc.)
        on_book_ticker as _on_bt,
        on_partial_depth as _on_depth,
    )
except Exception:
    # ── Minimal fallback (lets the app boot without market_data.*) ──
    class _MiniBookTracker:
        """
        Minimal L1/L2 quote tracker with subscriptions.
        API surface used by the app:
          - subscribe() -> asyncio.Queue[dict]
          - unsubscribe(queue)
          - update_book_ticker(...)
          - update_partial_depth(...)
          - get_quote(symbol)
          - get_quotes(symbols?)
        """
        def __init__(self) -> None:
            self._lock = asyncio.Lock()
            self._quotes: Dict[str, Dict[str, Any]] = {}
            self._subscribers: List[asyncio.Queue[Dict[str, Any]]] = []

        # ----- subscriptions -----
        async def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
            q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
            async with self._lock:
                self._subscribers.append(q)
            return q

        async def unsubscribe(self, q: asyncio.Queue[Dict[str, Any]]) -> None:
            async with self._lock:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass

        async def _broadcast(self, evt: Dict[str, Any]) -> None:
            async with self._lock:
                subs = list(self._subscribers)
            for qq in subs:
                try:
                    qq.put_nowait(dict(evt))
                except Exception:
                    pass  # never break on client queue issues

        # ----- L1 update -----
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
                # keep existing depth arrays if any
                bids = prev.get("bids")
                asks = prev.get("asks")
                if bids is not None:
                    evt["bids"] = bids
                if asks is not None:
                    evt["asks"] = asks
                self._quotes[sym] = evt
            await self._broadcast(evt)

        # ----- L2 update -----
        async def update_partial_depth(
            self,
            symbol: str,
            bids: Sequence[Tuple[float, float]],
            asks: Sequence[Tuple[float, float]],
            ts_ms: Optional[int] = None,
            keep_levels: int = 10,
        ) -> None:
            """
            Store top-of-book levels (price, qty). We sort:
              - bids: by price DESC
              - asks: by price ASC
            and clip to keep_levels.
            """
            sym = (symbol or "").upper()
            ts = ts_ms if ts_ms is not None else int(time.time() * 1000)

            # normalize & sort (keep only positive price/qty)
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
                # derive L1 if missing
                if "bid" not in entry and nbids:
                    entry["bid"] = nbids[0][0]
                    entry["bidQty"] = nbids[0][1]
                if "ask" not in entry and nasks:
                    entry["ask"] = nasks[0][0]
                    entry["askQty"] = nasks[0][1]
                entry["ts_ms"] = int(ts)
                self._quotes[sym] = entry

            await self._broadcast({"symbol": sym, **self._quotes[sym]})

        # ----- reads -----
        async def get_quote(self, symbol: str) -> Dict[str, Any]:
            async with self._lock:
                return dict(self._quotes.get((symbol or "").upper(), {}))

        async def get_quotes(self, symbols: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
            async with self._lock:
                if not symbols:
                    return [dict(v) for v in self._quotes.values()]
                sel: List[str] = []
                seen: Set[str] = set()
                for s in symbols:
                    sym = (s or "").upper()
                    if sym and sym not in seen:
                        seen.add(sym)
                        sel.append(sym)
                return [dict(v) for k, v in self._quotes.items() if k in sel]

    book_tracker = _MiniBookTracker()  # type: ignore

    async def _on_bt(symbol: str, bid: float, bid_qty: float, ask: float, ask_qty: float, ts_ms: Optional[int]) -> None:
        await book_tracker.update_book_ticker(symbol, bid, bid_qty, ask, ask_qty, ts_ms=ts_ms)

    async def _on_depth(symbol: str, bids: Sequence[Tuple[float, float]], asks: Sequence[Tuple[float, float]], ts_ms: Optional[int]) -> None:
        await book_tracker.update_partial_depth(symbol, bids, asks, ts_ms=ts_ms, keep_levels=10)


# ── Public API ───────────────────────────────────────────────────────────────

def _with_derived(q: Dict[str, Any]) -> Dict[str, Any]:
    """
    Stable shape for UI & executors with derived metrics.
    Keys: symbol, bid, ask, bidQty, askQty, ts_ms, mid, spread, spread_bps
    Also passes through 'bids'/'asks' when present.
    """
    if not q:
        return {}

    sym = (q.get("symbol") or "").upper()
    bid = float(q.get("bid") or 0.0)
    ask = float(q.get("ask") or 0.0)
    bid_qty = float(q.get("bidQty") or q.get("bid_qty") or 0.0)
    ask_qty = float(q.get("askQty") or q.get("ask_qty") or 0.0)

    # Only set a "now" timestamp if we actually have a price; otherwise keep 0
    ts_raw = q.get("ts_ms") or q.get("ts") or 0
    try:
        ts_i = int(ts_raw)
    except Exception:
        ts_i = 0
    if (bid > 0.0 or ask > 0.0):
        ts_ms = ts_i if ts_i > 0 else int(time.time() * 1000)
    else:
        ts_ms = 0

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
    if "bids" in q:
        out["bids"] = q["bids"]
    if "asks" in q:
        out["asks"] = q["asks"]
    return out


async def on_book_ticker(
    symbol: str,
    bid: float,
    bid_qty: float,
    ask: float,
    ask_qty: float,
    ts_ms: Optional[int] = None,
) -> None:
    """WS/REST callback → updates top-of-book."""
    await _on_bt(symbol, bid, bid_qty, ask, ask_qty, ts_ms)


async def on_partial_depth(
    symbol: str,
    bids: Sequence[Tuple[float, float]],
    asks: Sequence[Tuple[float, float]],
    ts_ms: Optional[int] = None,
) -> None:
    """WS/REST callback → updates L2 (if enabled)."""
    await _on_depth(symbol, bids, asks, ts_ms)


async def get_quote(symbol: str) -> Dict[str, Any]:
    raw = await book_tracker.get_quote(symbol)
    return _with_derived(raw) if raw else {}


async def get_all_quotes(symbols: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    raws = await book_tracker.get_quotes(symbols)
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for q in raws:
        sym = (q.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        qd = _with_derived(q)
        # Filter out placeholders (bid==ask==0).
        if (qd.get("bid", 0.0) > 0.0) or (qd.get("ask", 0.0) > 0.0):
            out.append(qd)
            seen.add(sym)
    return out


# ── Coalesced streaming for SSE ─────────────────────────────────────────────

# ── Coalesced streaming for SSE ─────────────────────────────────────────────
async def stream_quote_batches(
    symbols: Sequence[str],
    interval_ms: int = 500,
) -> AsyncGenerator[List[Dict[str, Any]], None]:
    """
    Yield latest quotes every interval_ms, already normalized with _with_derived.
    Ensures each yielded quote carries L2 ('bids'/'asks'):
      1) try from tracker cache,
      2) if still missing, fetch from REST depth and attach (best-effort).
    """
    # Normalize/unique symbols once
    want: List[str] = []
    seen_syms: Set[str] = set()
    for s in symbols:
        sym = (s or "").upper()
        if sym and sym not in seen_syms:
            seen_syms.add(sym)
            want.append(sym)

    queue: asyncio.Queue[Dict[str, Any]] = await book_tracker.subscribe()
    try:
        latest: Dict[str, Dict[str, Any]] = {}

        async def drain_once(timeout: float) -> None:
            """Drain updates for 'timeout' seconds, keep only the latest per symbol."""
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
                latest[sym] = evt  # keep the most recent per symbol

        period = max(0.05, min(interval_ms / 1000.0, 60.0))

        # Reuse one HTTP client for depth lookups while this generator is alive
        import httpx  # already imported at module scope, but safe here too
        async with httpx.AsyncClient(
            base_url="https://api.mexc.com",
            headers={"Accept": "application/json"},
            timeout=5.0,
        ) as depth_client:
            while True:
                await drain_once(period)

                if not latest and want:
                    batch = await get_all_quotes(want)
                else:
                    batch = [
                        q for q in (_with_derived(v) for v in latest.values())
                        if (q.get("bid", 0.0) > 0.0) or (q.get("ask", 0.0) > 0.0)
                    ]
                    latest.clear()

                if not batch:
                    continue

                # Try to enrich from tracker first
                need_l2 = [q["symbol"] for q in batch if not q.get("bids") or not q.get("asks")]
                if need_l2:
                    try:
                        raws = await book_tracker.get_quotes(need_l2)  # type: ignore[attr-defined]
                        by_sym = { (r.get("symbol") or "").upper(): r for r in raws }
                        for q in batch:
                            raw = by_sym.get(q["symbol"])
                            if not raw:
                                continue
                            if raw.get("bids") and not q.get("bids"):
                                q["bids"] = raw["bids"]
                            if raw.get("asks") and not q.get("asks"):
                                q["asks"] = raw["asks"]
                    except Exception:
                        pass  # best-effort

                # If still missing, fetch from REST and attach + persist via _on_depth
                still_missing = [q["symbol"] for q in batch if not q.get("bids") or not q.get("asks")]
                if still_missing:
                    try:
                        tasks = [asyncio.create_task(_fetch_depth(depth_client, s, limit=10)) for s in still_missing]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        for res in results:
                            if isinstance(res, Exception) or res is None:
                                continue
                            sym = (res.get("symbol") or "").upper()
                            # write back to tracker so future snapshots have L2 immediately
                            try:
                                await _on_depth(sym, res.get("bids", []), res.get("asks", []), ts_ms=res.get("ts_ms"))
                            except Exception:
                                pass
                            # attach to the batch we are about to yield
                            for q in batch:
                                if q["symbol"] == sym:
                                    if res.get("bids"):
                                        q["bids"] = res["bids"]
                                    if res.get("asks"):
                                        q["asks"] = res["asks"]
                    except Exception:
                        pass  # best-effort depth fill

                # Only yield if we have quotes (with or without L2). UI will use L2 when present.
                if batch:
                    yield batch
    finally:
        await book_tracker.unsubscribe(queue)



# ── WS manager + REST fallback ──────────────────────────────────────────────

# WS client (preferred)
try:
    from app.market_data.ws_client import (
        MEXCWebSocketClient as _WSClient,
        PROTO_AVAILABLE as _WS_PROTO_OK,
    )
except Exception:
    _WSClient = None
    _WS_PROTO_OK = False

# REST fallback
import httpx

_REST_POLL_TASK: Optional[asyncio.Task[None]] = None
_SUBSCRIBED: Set[str] = set()

_WS_TASK: Optional[asyncio.Task[None]] = None
_WS_CLIENT: Optional[Any] = None   # instance of MEXCWebSocketClient when WS is used
_WS_WANTED: Set[str] = set()
_WS_RUNNING: Set[str] = set()

# Depth refresher (active in WS mode to guarantee L2 updates)
_DEPTH_TASK: Optional[asyncio.Task[None]] = None
_DEPTH_SUBSCRIBED: Set[str] = set()


async def _fetch_ticker(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
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


async def _fetch_depth(client: httpx.AsyncClient, symbol: str, limit: int = 10) -> Optional[Dict[str, Any]]:
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
            try:
                p = float(it[0]); q = float(it[1])
                if p > 0 and q > 0:
                    bids.append((p, q))
            except Exception:
                continue
        for it in asks_raw:
            try:
                p = float(it[0]); q = float(it[1])
                if p > 0 and q > 0:
                    asks.append((p, q))
            except Exception:
                continue
        ts_ms = int(time.time() * 1000)
        return {"symbol": symbol, "bids": bids, "asks": asks, "ts_ms": ts_ms}
    except Exception:
        return None


async def _rest_poller_loop() -> None:
    base = "https://api.mexc.com"
    depth_min_period_s = 0.8
    last_depth_at: Dict[str, float] = {}
    try:
        async with httpx.AsyncClient(base_url=base, headers={"Accept": "application/json"}) as client:
            while True:
                syms = list(_SUBSCRIBED)
                if not syms:
                    await asyncio.sleep(0.5)
                    continue

                # ticker batch
                ticker_tasks = [asyncio.create_task(_fetch_ticker(client, s)) for s in syms]
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

                # depth batch
                depth_tasks: List[asyncio.Task[Optional[Dict[str, Any]]]] = []
                now = time.monotonic()
                for s in syms:
                    if now - last_depth_at.get(s, 0.0) >= depth_min_period_s:
                        last_depth_at[s] = now
                        depth_tasks.append(asyncio.create_task(_fetch_depth(client, s, limit=10)))
                if depth_tasks:
                    depth_res = await asyncio.gather(*depth_tasks, return_exceptions=True)
                    for res in depth_res:
                        if isinstance(res, Exception) or res is None:
                            continue
                        d = res
                        await _on_depth(
                            d["symbol"],
                            d.get("bids", []),
                            d.get("asks", []),
                            ts_ms=d.get("ts_ms"),
                        )

                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        return
    except Exception:
        await asyncio.sleep(1.0)


async def _depth_refresher_loop() -> None:
    """
    WS mode depth drip: fetch /depth periodically so L2 is always populated
    even if the WS client doesn't deliver DEPTH messages.
    """
    base = "https://api.mexc.com"
    min_period_s = 0.9
    last_at: Dict[str, float] = {}
    try:
        async with httpx.AsyncClient(base_url=base, headers={"Accept": "application/json"}) as client:
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
                        tasks.append(asyncio.create_task(_fetch_depth(client, s, limit=10)))
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
    """Best-effort single-pass L1+L2 seed, used in both WS and REST modes."""
    try:
        async with httpx.AsyncClient(base_url="https://api.mexc.com", headers={"Accept": "application/json"}) as client:
            # L1
            t_tasks = [asyncio.create_task(_fetch_ticker(client, s)) for s in symbols]
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
            # L2
            d_tasks = [asyncio.create_task(_fetch_depth(client, s, limit=10)) for s in symbols]
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
        try:
            await _REST_POLL_TASK
        except Exception:
            pass
    _REST_POLL_TASK = None


async def _start_rest_poller() -> None:
    global _REST_POLL_TASK
    if _REST_POLL_TASK is None or _REST_POLL_TASK.done():
        _REST_POLL_TASK = asyncio.create_task(_rest_poller_loop())


async def _stop_depth_refresher() -> None:
    global _DEPTH_TASK, _DEPTH_SUBSCRIBED
    if _DEPTH_TASK and not _DEPTH_TASK.done():
        _DEPTH_TASK.cancel()
        try:
            await _DEPTH_TASK
        except Exception:
            pass
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
        try:
            await _WS_TASK
        except Exception:
            pass
    _WS_TASK = None
    _WS_CLIENT = None
    _WS_RUNNING = set()


async def _start_ws(symbols: Set[str]) -> None:
    """Start a new WS client for the given symbol set (restarts if running)."""
    global _WS_TASK, _WS_CLIENT, _WS_RUNNING
    if not _WSClient or not _WS_PROTO_OK:
        return
    await _stop_ws()
    _WS_CLIENT = _WSClient(sorted(symbols), channels=["BOOK_TICKER", "DEPTH_LIMIT"])  # type: ignore[call-arg]
    _WS_TASK = asyncio.create_task(_WS_CLIENT.run())  # type: ignore[arg-type]
    _WS_RUNNING = set(symbols)


async def ensure_symbols_subscribed(symbols: Sequence[str]) -> None:
    """
    Make sure we are ingesting updates for requested symbols.
    Prefer WS feed if protobuf decoders are available; otherwise use REST poller.
    Also seeds L1/L2 immediately for better snapshots.
    """
    # normalize/unique
    norm: Set[str] = set((s or "").upper() for s in symbols if (s or "").strip())
    if not norm:
        return

    # Prefer WS when available
    if _WSClient and _WS_PROTO_OK:
        _WS_WANTED.update(norm)
        if (_WS_TASK is None or _WS_TASK.done()) or (not _WS_WANTED.issubset(_WS_RUNNING) or not _WS_RUNNING.issubset(_WS_WANTED)):
            await _start_ws(_WS_WANTED)
        # Seed immediately so first snapshot has L1+L2
        await _rest_seed_symbols(list(norm))
        # Ensure REST ticker poller is off
        await _stop_rest_poller()
        # Start depth refresher in WS mode (guarantees L2 updates)
        _DEPTH_SUBSCRIBED.update(norm)
        await _start_depth_refresher()
        return

    # Otherwise: REST fallback (start poller and seed once)
    _SUBSCRIBED.update(norm)
    await _start_rest_poller()
    await _rest_seed_symbols(list(norm))
    # No extra depth refresher in REST mode (poller already does depth)
    await _stop_depth_refresher()
