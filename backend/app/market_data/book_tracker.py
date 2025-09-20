# app/market_data/book_tracker.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, AsyncIterator

# ───────────────────────────── helpers ─────────────────────────────

def now_ms() -> int:
    return int(time.time() * 1000)


def bps(a: float, b: float) -> float:
    """
    basis points between two prices a (higher) and b (lower): (a-b)/mid * 10_000
    Для спреда используем mid = (a+b)/2.
    """
    if a <= 0.0 or b <= 0.0:
        return 0.0
    m = 0.5 * (a + b)
    if m <= 0.0:
        return 0.0
    return (a - b) / m * 10_000.0


# ───────────────────────────── models ─────────────────────────────

@dataclass
class TopOfBook:
    bid: float = 0.0
    bid_qty: float = 0.0
    ask: float = 0.0
    ask_qty: float = 0.0
    ts_ms: int = 0  # биржевой/серверный timestamp последнего апдейта


@dataclass
class L2Book:
    """
    Лёгкий L2-срез стакана (например L10).
    bids: отсортированы по цене убыванию (best -> worse)
    asks: отсортированы по цене возрастанию (best -> worse)
    """
    bids: List[Tuple[float, float]] = field(default_factory=list)  # (price, qty)
    asks: List[Tuple[float, float]] = field(default_factory=list)
    ts_ms: int = 0


@dataclass
class SymbolState:
    top: TopOfBook = field(default_factory=TopOfBook)
    l2: Optional[L2Book] = None
    depth_enabled: bool = False  # хотим ли держать L10 для символа


# ───────────────────────────── tracker ─────────────────────────────

class BookTracker:
    """
    Хранилище маркет-данных для стратегии и API:
    - update_book_ticker(...) — обновляет top-of-book
    - update_partial_depth(...) — обновляет L10 стакан (по запросу)
    - set_quote(...) — совместимость со старым REST-пуллером (last, bid, ask)
    - get_quote(...) / get_quotes(...) / get_all()
    - subscribe()/unsubscribe() — подписки для SSE/WS (через очередь)
    - subscribe_stream() — асинхронный генератор событий (удобно для SSE/WS)
    - compute_metrics(...) — считает spread, spread_bps, imbalance, microprice, absorption@Xbps
    """

    def __init__(self) -> None:
        self._states: Dict[str, SymbolState] = {}
        self._lock = asyncio.Lock()
        self._subscribers: List[asyncio.Queue] = []
        self._sub_qsize = 256  # per-subscriber backpressure cap

    # ───────────────── subscriptions (для SSE/WS) ─────────────────

    async def subscribe(self) -> asyncio.Queue:
        """
        Возвращает очередь для получения событий. При переполнении в broadcast
        удаляется самый старый элемент (чтобы не расти по памяти).
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._sub_qsize)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    async def subscribe_stream(self) -> AsyncIterator[Dict[str, Any]]:
        """
        Удобный асинхронный генератор: подключился → получай бесконечный поток событий.
        Освобождает подписку автоматически.
        """
        q = await self.subscribe()
        try:
            while True:
                evt = await q.get()
                yield evt
        finally:
            await self.unsubscribe(q)

    async def _broadcast(self, payload: Dict[str, Any]) -> None:
        """
        Рассылает событие всем подписчикам. Если очередь переполнена — дропаем
        самый старый элемент и кладём новый (держим стрим «свежим»).
        """
        dead: List[asyncio.Queue] = []
        async with self._lock:
            for q in self._subscribers:
                try:
                    if q.full():
                        # drop oldest to keep latency low
                        _ = q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    dead.append(q)
            if dead:
                self._subscribers = [q for q in self._subscribers if q not in dead]

    # ───────────────── updates ─────────────────

    async def enable_depth(self, symbol: str, enabled: bool = True) -> None:
        sym = symbol.upper()
        async with self._lock:
            st = self._states.setdefault(sym, SymbolState())
            st.depth_enabled = enabled
            if enabled and st.l2 is None:
                st.l2 = L2Book()

    async def update_book_ticker(
        self,
        symbol: str,
        bid: float,
        bid_qty: float,
        ask: float,
        ask_qty: float,
        ts_ms: Optional[int] = None,
    ) -> None:
        sym = symbol.upper()
        t = ts_ms if ts_ms is not None else now_ms()
        async with self._lock:
            st = self._states.setdefault(sym, SymbolState())
            st.top = TopOfBook(
                bid=max(0.0, float(bid)),
                bid_qty=max(0.0, float(bid_qty)),
                ask=max(0.0, float(ask)),
                ask_qty=max(0.0, float(ask_qty)),
                ts_ms=int(t),
            )
            payload = self._snapshot_locked(sym, st)
        await self._broadcast(payload)

    async def update_partial_depth(
        self,
        symbol: str,
        bids: Sequence[Tuple[float, float]],
        asks: Sequence[Tuple[float, float]],
        ts_ms: Optional[int] = None,
        keep_levels: int = 10,
    ) -> None:
        """
        Обновляет лёгкий стакан (частичный snapshot), обрезая до keep_levels.
        Ожидается, что bids отсортированы DESC, asks ASC (как приходит из источника).
        """
        sym = symbol.upper()
        t = ts_ms if ts_ms is not None else now_ms()
        # нормализуем и фильтруем невалидные строки
        nbids: List[Tuple[float, float]] = []
        nasks: List[Tuple[float, float]] = []
        for p, q in bids[: keep_levels * 2]:  # чуть с запасом, режем потом
            if p > 0 and q > 0:
                nbids.append((float(p), float(q)))
        for p, q in asks[: keep_levels * 2]:
            if p > 0 and q > 0:
                nasks.append((float(p), float(q)))

        # сортировки на всякий случай
        nbids.sort(key=lambda x: x[0], reverse=True)
        nasks.sort(key=lambda x: x[0])

        nbids = nbids[:keep_levels]
        nasks = nasks[:keep_levels]

        async with self._lock:
            st = self._states.setdefault(sym, SymbolState())
            if not st.depth_enabled:
                return
            if st.l2 is None:
                st.l2 = L2Book()
            st.l2.bids = nbids
            st.l2.asks = nasks
            st.l2.ts_ms = int(t)
            payload = self._snapshot_locked(sym, st)
        await self._broadcast(payload)

    # Совместимость с прежним REST-пуллером (last, bid, ask)
    async def set_quote(self, symbol: str, last: float | None, bid: float | None, ask: float | None) -> None:
        sym = symbol.upper()
        t = now_ms()
        b = float(bid or 0.0)
        a = float(ask or 0.0)
        async with self._lock:
            st = self._states.setdefault(sym, SymbolState())
            # qty неизвестны — поставим 0.0
            st.top = TopOfBook(
                bid=b,
                bid_qty=0.0,
                ask=a,
                ask_qty=0.0,
                ts_ms=t,
            )
            payload = self._snapshot_locked(sym, st)
        await self._broadcast(payload)

    # ───────────────── reads ─────────────────

    async def get_quote(self, symbol: str, absorption_x_bps: float = 10.0) -> Dict[str, Any]:
        sym = symbol.upper()
        async with self._lock:
            st = self._states.get(sym)
            if not st:
                return self._empty_snapshot(sym)
            return self._snapshot_locked(sym, st, absorption_x_bps)

    async def get_quotes(
        self,
        symbols: Optional[Sequence[str]] = None,
        absorption_x_bps: float = 10.0,
    ) -> List[Dict[str, Any]]:
        """
        Возвращает список котировок/метрик для указанного набора символов (или для всех известных).
        """
        syms: List[str]
        if symbols:
            syms = [s.upper() for s in symbols]
        else:
            syms = list(self._states.keys())

        out: List[Dict[str, Any]] = []
        async with self._lock:
            for sym in syms:
                st = self._states.get(sym)
                if not st:
                    out.append(self._empty_snapshot(sym))
                    continue
                out.append(self._snapshot_locked(sym, st, absorption_x_bps))
        return out

    # Совместимость с роутером /api/market/quotes
    async def get_all(self) -> List[Dict[str, Any]]:
        return await self.get_quotes()

    async def reset_symbol(self, symbol: str) -> None:
        async with self._lock:
            self._states.pop(symbol.upper(), None)

    # ───────────────── metrics & snapshots ─────────────────

    def _snapshot_locked(self, sym: str, st: SymbolState, absorption_x_bps: float = 10.0) -> Dict[str, Any]:
        top = st.top
        metrics = self._compute_metrics_locked(st, absorption_x_bps=absorption_x_bps)
        return {
            "symbol": sym,
            "bid": top.bid,
            "bidQty": top.bid_qty,
            "ask": top.ask,
            "askQty": top.ask_qty,
            **metrics,
            "ts_ms": top.ts_ms or (st.l2.ts_ms if st.l2 else 0),
        }

    @staticmethod
    def _empty_snapshot(sym: str) -> Dict[str, Any]:
        return {
            "symbol": sym,
            "bid": 0.0,
            "bidQty": 0.0,
            "ask": 0.0,
            "askQty": 0.0,
            "mid": 0.0,
            "spread": 0.0,
            "spread_bps": 0.0,
            "imbalance": 0.0,
            "microprice": 0.0,
            "absorption_bid_usd": 0.0,
            "absorption_ask_usd": 0.0,
            "ts_ms": 0,
        }

    def _compute_metrics_locked(self, st: SymbolState, absorption_x_bps: float = 10.0) -> Dict[str, Any]:
        """
        Расчёт метрик для стратегии:
        - mid, spread, spread_bps
        - imbalance
        - microprice
        - absorption_bid/ask (в USD) до смещения mid на X bps
        """
        top = st.top
        bid, ask = float(top.bid), float(top.ask)
        bid_q, ask_q = float(top.bid_qty), float(top.ask_qty)

        mid = 0.5 * (bid + ask) if (bid > 0 and ask > 0) else 0.0
        spr = (ask - bid) if (bid > 0 and ask > 0) else 0.0
        spr_bps = bps(ask, bid) if spr > 0 else 0.0

        denom = max(1e-12, bid_q + ask_q)
        imbalance = (bid_q / denom) if denom > 0 else 0.0

        # microprice: (a*qb + b*qa)/(qb+qa)
        microprice = 0.0
        if denom > 0 and bid > 0 and ask > 0:
            microprice = (ask * bid_q + bid * ask_q) / denom

        # absorption оценим на основе L10, если доступно
        abs_bid_usd, abs_ask_usd = 0.0, 0.0
        if st.l2 and st.depth_enabled and mid > 0 and absorption_x_bps > 0:
            abs_bid_usd = self._absorption_notional_bid(st.l2.bids, mid, absorption_x_bps)
            abs_ask_usd = self._absorption_notional_ask(st.l2.asks, mid, absorption_x_bps)

        return {
            "mid": mid,
            "spread": spr,
            "spread_bps": spr_bps,
            "imbalance": imbalance,
            "microprice": microprice,
            "absorption_bid_usd": abs_bid_usd,
            "absorption_ask_usd": abs_ask_usd,
        }

    @staticmethod
    def _absorption_notional_bid(bids: Sequence[Tuple[float, float]], mid: float, x_bps: float) -> float:
        """
        Сколько можно ПРОДАТЬ (ударяя по bid-уровням) до того, как цена mid
        сместится на X bps вниз. threshold = mid * (1 - X_bps/10000).
        """
        if mid <= 0 or x_bps <= 0:
            return 0.0
        threshold = mid * (1.0 - x_bps / 10_000.0)
        notional = 0.0
        for price, qty in bids:
            if price < threshold:
                break
            if price > 0 and qty > 0:
                notional += price * qty
        return notional

    @staticmethod
    def _absorption_notional_ask(asks: Sequence[Tuple[float, float]], mid: float, x_bps: float) -> float:
        """
        Сколько можно КУПИТЬ (ударяя по ask-уровням) до того, как mid
        сместится на X bps вверх. threshold = mid * (1 + X_bps/10000).
        """
        if mid <= 0 or x_bps <= 0:
            return 0.0
        threshold = mid * (1.0 + x_bps / 10_000.0)
        notional = 0.0
        for price, qty in asks:
            if price > threshold:
                break
            if price > 0 and qty > 0:
                notional += price * qty
        return notional


# ───────────────────────────── singleton & WS-callbacks ─────────────────────────────

book_tracker = BookTracker()

async def on_book_ticker(symbol: str, bid: float, bid_qty: float, ask: float, ask_qty: float, ts_ms: Optional[int]) -> None:
    """
    WS callback-обёртка: обновляет top-of-book (совместима с ws_client.py)
    """
    await book_tracker.update_book_ticker(symbol, bid, bid_qty, ask, ask_qty, ts_ms=ts_ms)

async def on_partial_depth(symbol: str, bids: Sequence[Tuple[float, float]], asks: Sequence[Tuple[float, float]], ts_ms: Optional[int]) -> None:
    """
    WS callback-обёртка: обновляет лёгкий стакан (совместима с ws_client.py)
    """
    await book_tracker.update_partial_depth(symbol, bids, asks, ts_ms=ts_ms)


# ───────────────────────────── convenience wrappers for routers ─────────────────────────────

def snapshot_all_quotes() -> Dict[str, Any]:
    """
    Быстрый снимок для REST: dict {symbol -> snapshot}
    """
    # NB: здесь неблокирующий доступ, так как _snapshot_locked требует state.
    # Используем публичный метод get_all() асинхронно извне (см. роутер).
    # Эта обёртка оставлена для совместимости с предыдущим вариантом.
    # В актуальном коде роутер должен вызывать: await book_tracker.get_all()
    return {}  # оставлено пустым намеренно (см. комментарий выше)

async def subscribe_quotes() -> AsyncIterator[Dict[str, Any]]:
    """
    Асинхронный генератор событий для SSE/WS.
    """
    async for evt in book_tracker.subscribe_stream():
        yield evt
