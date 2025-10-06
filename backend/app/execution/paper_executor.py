# app/execution/paper_executor.py
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from decimal import Decimal, getcontext, ROUND_DOWN
from typing import Any, Dict, Optional, Protocol, Tuple

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.services import book_tracker as bt_service
from app.services.book_tracker import ensure_symbols_subscribed
from app.models.orders import Order, OrderSide, OrderType, OrderStatus, TimeInForce
from app.models.fills import Fill, FillSide, Liquidity
from app.models.positions import Position, PositionSide, PositionStatus

from datetime import datetime, timezone
from app.pnl.service import PnlService


class PositionTrackerProto(Protocol):
    def on_fill(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        ts_ms: int,
        strategy_tag: str,
        fee: Decimal | float | int | None,
        fee_asset: Optional[str],
        client_order_id: Optional[str],
        exchange_order_id: Optional[str],
        trade_id: Optional[str],
        executed_at: Optional[datetime],
        exchange: Optional[str],
        account_id: Optional[str],
    ) -> None: ...


# sane precision for price/qty math (matches Numeric(28,12))
getcontext().prec = 34


@dataclass
class MemPosition:
    symbol: str
    qty: Decimal = Decimal("0")        # >0 long, <0 short (paper keeps long-only)
    avg_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    ts_ms: int = 0


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dec(x: float | str | Decimal | None) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


# ---------- rounding helpers ----------
def _qty_places(symbol: str) -> int:
    # в реале лучше тянуть шаг из биржи; для paper — 6 знаков
    return 6


def _qty_quantum(symbol: str) -> Decimal:
    return Decimal("1").scaleb(-_qty_places(symbol))  # e.g. 1e-6


def _round_qty(symbol: str, qty: Decimal) -> Decimal:
    if qty <= 0:
        return Decimal("0")
    quant = _qty_quantum(symbol)
    rounded = qty.quantize(quant, rounding=ROUND_DOWN)
    if rounded <= 0 and qty > 0:
        rounded = quant
    return rounded


async def _detect_price_places(symbol: str) -> int:
    """Pick decimal places based on price level so cheap coins get enough precision."""
    try:
        q = await bt_service.get_quote(symbol.upper())
        bid = _dec(q.get("bid", 0.0))
        ask = _dec(q.get("ask", 0.0))
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)
    except Exception:
        mid = Decimal("0")

    if mid <= 0:
        return 4
    if mid >= Decimal("1"):
        return 2
    if mid >= Decimal("0.1"):
        return 5
    return 6


async def _round_price_async(symbol: str, price: Decimal) -> Decimal:
    if price <= 0:
        return Decimal("0")
    places = await _detect_price_places(symbol)
    quant = Decimal("1").scaleb(-places)
    return price.quantize(quant, rounding=ROUND_DOWN)


def _split_symbol(symbol: str) -> Tuple[str, str]:
    s = symbol.upper()
    for tail in ("USDT", "USDC", "FDUSD", "BUSD"):
        if s.endswith(tail):
            return s[:-len(tail)], tail
    return s[:-3] or s, s[-3:] or "USDT"


def _is_usd_quote(quote: str) -> bool:
    return quote in {"USDT", "USDC", "BUSD", "FDUSD"}


# ---------- debug helpers ----------
def _dbg_enabled() -> bool:
    return str(os.getenv("PAPER_DEBUG", "0")).lower() in {"1", "true", "yes", "on"}


def _dbg(msg: str) -> None:
    if _dbg_enabled():
        print(f"[PAPER] {msg}")


class PaperExecutor:
    """
    Paper execution simulator with DB persistence (spot, long-only).

    Guards:
      - Global exposure cap via settings.max_exposure_usd (0 = disabled)
      - Per-symbol exposure cap via env MAX_PER_SYMBOL_USD (0 = disabled)
      - Price/Qty rounding tuned for spot tick/step sizes
    """

    def __init__(
        self,
        session_factory: Optional[SessionFactory] = None,
        workspace_id: int = 1,
        position_tracker: Optional[PositionTrackerProto] = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._positions: Dict[str, MemPosition] = {}
        self._session_factory = session_factory
        self._wsid = workspace_id
        self._pnl = PnlService()
        self._tracker = position_tracker

        # Config
        try:
            self._max_per_symbol_usd = Decimal(str(os.getenv("MAX_PER_SYMBOL_USD", "0") or "0"))
        except Exception:
            self._max_per_symbol_usd = Decimal("0")

        # Seed memory from DB OPEN positions so paper survives restarts
        if self._session_factory:
            try:
                self._hydrate_from_db()
            except Exception:
                pass

    # -------- StrategyEngine interface --------

    async def start_symbol(self, symbol: str) -> None:
        # best-effort: разогреть котировки
        try:
            await ensure_symbols_subscribed([symbol.upper()])
        except Exception:
            pass

    async def stop_symbol(self, symbol: str) -> None:
        # для paper сейчас нечего отменять — ордеров как таковых нет
        return None

    async def flatten_symbol(self, symbol: str) -> None:
        """Закрыть текущую лонг-позицию по bid (или mid/avg как фоллбек)."""
        sym = symbol.upper()

        async with self._lock:
            pos = self._positions.get(sym)
            if not pos or pos.qty <= Decimal("0"):
                return
            close_qty = pos.qty
            prev_avg = pos.avg_price  # snapshot for PnL calc

        try:
            await ensure_symbols_subscribed([sym])
        except Exception:
            pass

        q = await self._get_quote_with_retries(sym)
        if q is None:
            _dbg(f"flatten rejected: no quote after retries for {sym}")
            return

        bid = _dec(q.get("bid", 0.0))
        ask = _dec(q.get("ask", 0.0))
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)

        # Closing LONG -> SELL at BID; fallback to mid/prev_avg
        exit_px = bid if bid > 0 else (mid if mid > 0 else prev_avg)
        exit_px = await _round_price_async(sym, exit_px)
        close_qty = _round_qty(sym, close_qty)

        # игнорируем микроскопические "пылинки"
        if exit_px <= 0 or close_qty <= Decimal("0"):
            _dbg(f"flatten rejected: px={exit_px} qty={close_qty}")
            return

        await self._fill_and_persist(
            symbol=sym,
            side="SELL",
            fill_price=exit_px,
            qty=close_qty,
            strategy_tag="flatten",
            prev_avg_for_pnl=prev_avg,
        )

        async with self._lock:
            mp = self._positions.setdefault(sym, MemPosition(symbol=sym))
            mp.qty = Decimal("0")
            mp.avg_price = Decimal("0")
            mp.ts_ms = _now_ms()

    async def cancel_orders(self, symbol: str) -> None:
        # в paper ордеров нет — NOP
        return None

    async def place_maker(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        tag: str = "mm",
    ) -> Optional[str]:
        """
        Spot maker simulation (long-only):
          BUY  -> исполняем по BID (или MID/PROVIDED как фоллбек)
          SELL -> исполняем по ASK (или MID/PROVIDED как фоллбек)
        """
        sym = symbol.upper()
        s_up = side.upper().strip()
        qty_raw = _dec(qty)
        qty_dec = _round_qty(sym, qty_raw)
        if qty_dec <= 0:
            _dbg(f"reject: qty <= 0 after rounding (req={qty_raw}, step={_qty_quantum(sym)})")
            return None

        # ensure quotes flow
        try:
            await ensure_symbols_subscribed([sym])
        except Exception:
            pass

        q = await self._get_quote_with_retries(sym)
        if q is None:
            _dbg(f"reject: no quote after retries for {sym}")
            return None

        bid = _dec(q.get("bid", 0.0))
        ask = _dec(q.get("ask", 0.0))
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)

        provided = _dec(price)

        # По умолчанию — «лучшая сторона спреда», provided используем как мягкий фоллбек.
        if s_up == "BUY":
            candidate = bid if bid > 0 else (mid if mid > 0 else (provided if provided > 0 else bid))
        else:  # SELL
            candidate = ask if ask > 0 else (mid if mid > 0 else (provided if provided > 0 else ask))

        fill_price = await _round_price_async(sym, candidate)
        if fill_price <= 0:
            _dbg(f"reject: no price available (bid={bid}, ask={ask}, mid={mid}, provided={provided})")
            return None

        # ---------- Global exposure guard (BUY only) ----------
        if s_up == "BUY":
            try:
                max_expo = Decimal(str(settings.max_exposure_usd or 0))
            except Exception:
                max_expo = Decimal("0")
            if max_expo > 0:
                cur = await self._total_exposure_usd()
                addl = qty_dec * fill_price
                if (cur + addl) > max_expo:
                    _dbg(f"reject: global exposure {cur+addl:.8f} > limit {max_expo}")
                    return None

        # ---------- Per-symbol exposure guard (BUY only) ----------
        if s_up == "BUY" and self._max_per_symbol_usd > 0:
            cur_sym = await self._symbol_exposure_usd(sym)
            addl = qty_dec * fill_price
            if (cur_sym + addl) > self._max_per_symbol_usd:
                _dbg(f"reject: {sym} exposure {cur_sym+addl:.8f} > limit {self._max_per_symbol_usd}")
                return None

        # ---------- Long-only guards for SELL ----------
        prev_avg_for_pnl = None
        if s_up == "SELL":
            async with self._lock:
                prev = self._positions.get(sym) or MemPosition(symbol=sym)
                if prev.qty <= 0:
                    _dbg(f"reject SELL: no inventory for {sym}")
                    return None
                if qty_dec > prev.qty:
                    _dbg(f"reject SELL: requested {qty_dec} > held {prev.qty} for {sym}")
                    return None
                prev_avg_for_pnl = prev.avg_price

        coid = await self._fill_and_persist(
            symbol=sym,
            side=s_up,
            fill_price=fill_price,
            qty=qty_dec,
            strategy_tag=tag,
            prev_avg_for_pnl=prev_avg_for_pnl,
        )
        return coid

    async def get_position(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.upper()
        async with self._lock:
            pos = self._positions.get(sym) or MemPosition(symbol=sym)
            snap_qty = pos.qty
            snap_avg = pos.avg_price
            snap_real = pos.realized_pnl
            snap_ts = pos.ts_ms

        q = await bt_service.get_quote(sym)
        bid = _dec(q.get("bid", 0.0))
        ask = _dec(q.get("ask", 0.0))
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)

        upnl = (mid - snap_avg) * snap_qty if (snap_qty != 0 and mid > 0) else Decimal("0")
        return {
            "symbol": sym,
            "qty": float(snap_qty),
            "avg_price": float(snap_avg),
            "unrealized_pnl": float(upnl),
            "realized_pnl": float(snap_real),
            "ts_ms": snap_ts,
        }

    # -------- Internal helpers (DB + in-memory) --------

    def _hydrate_from_db(self) -> None:
        """Load OPEN long positions from DB into in-memory map (paper mode)."""
        session: Session = self._session_factory()  # type: ignore[call-arg]
        try:
            rows = (
                session.query(Position)
                .filter(
                    Position.workspace_id == self._wsid,
                    Position.is_open == True,             # noqa: E712
                    Position.status == PositionStatus.OPEN,
                    Position.side == PositionSide.BUY,    # paper engine is long-only
                )
                .all()
            )
            now_ms = _now_ms()
            for row in rows:
                sym = str(row.symbol).upper()
                qty = _dec(row.qty)
                if qty <= 0:
                    continue
                avg = _dec(row.entry_price)
                real = _dec(row.realized_pnl or 0)
                self._positions[sym] = MemPosition(
                    symbol=sym,
                    qty=qty,
                    avg_price=avg,
                    realized_pnl=real,
                    ts_ms=now_ms,
                )
        finally:
            session.close()

    async def _get_quote_with_retries(
        self, symbol: str, attempts: int = 5, delay_ms: int = 60
    ) -> Optional[Dict[str, Any]]:
        sym = symbol.upper()
        for i in range(max(1, attempts)):
            q = await bt_service.get_quote(sym)
            bid = _dec(q.get("bid", 0.0))
            ask = _dec(q.get("ask", 0.0))
            if bid > 0 or ask > 0:
                return q
            if i < attempts - 1:
                await asyncio.sleep(delay_ms / 1000)
        return None

    async def _total_exposure_usd(self) -> Decimal:
        async with self._lock:
            items = list(self._positions.items())
        total = Decimal("0")
        for sym, mp in items:
            if mp.qty == 0:
                continue
            q = await bt_service.get_quote(sym)
            bid = _dec(q.get("bid", 0.0))
            ask = _dec(q.get("ask", 0.0))
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)
            px = mid if mid > 0 else (mp.avg_price if mp.avg_price > 0 else Decimal("0"))
            if px > 0:
                total += abs(mp.qty) * px
        return total

    async def _symbol_exposure_usd(self, symbol: str) -> Decimal:
        async with self._lock:
            mp = self._positions.get(symbol)
        if not mp or mp.qty == 0:
            return Decimal("0")
        q = await bt_service.get_quote(symbol)
        bid = _dec(q.get("bid", 0.0))
        ask = _dec(q.get("ask", 0.0))
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)
        px = mid if mid > 0 else (mp.avg_price if mp.avg_price > 0 else Decimal("0"))
        if px <= 0:
            return Decimal("0")
        return abs(mp.qty) * px

    async def _fill_and_persist(
        self,
        symbol: str,
        side: str,
        fill_price: Decimal,
        qty: Decimal,
        strategy_tag: str,
        prev_avg_for_pnl: Optional[Decimal] = None,
    ) -> str:
        ts_ms = _now_ms()
        executed_at = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC for DB
        client_order_id = f"paper-{symbol}-{ts_ms}"

        # Snapshot previous avg/qty for accurate realized PnL (before memory update)
        async with self._lock:
            prev = self._positions.get(symbol) or MemPosition(symbol=symbol)
            prev_qty = prev.qty
            prev_avg = prev.avg_price

        await self._apply_memory_fill(symbol, side, fill_price, qty, ts_ms)

        # Notify durable tracker (does not break fills if fails)
        try:
            if self._tracker is not None:
                ex = getattr(settings, "active_provider", None) or "PAPER"
                self._tracker.on_fill(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=fill_price,
                    ts_ms=ts_ms,
                    strategy_tag=strategy_tag,
                    fee=Decimal("0"),
                    fee_asset="USDT",
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    trade_id=str(ts_ms),
                    executed_at=executed_at,
                    exchange=str(ex),
                    account_id="paper",
                )
        except Exception:
            pass

        if self._session_factory:
            session: Session = self._session_factory()
            try:
                order = Order(
                    workspace_id=self._wsid,
                    symbol=symbol,
                    side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                    type=OrderType.MARKET,
                    tif=TimeInForce.IOC,
                    qty=qty,
                    price=None,
                    filled_qty=qty,
                    avg_fill_price=fill_price,
                    status=OrderStatus.FILLED,
                    is_active=False,
                    strategy_tag=strategy_tag,
                    client_order_id=client_order_id,
                )
                session.add(order)
                session.flush()

                fill = Fill(
                    workspace_id=self._wsid,
                    order_id=order.id,
                    symbol=symbol,
                    side=FillSide.BUY if side == "BUY" else FillSide.SELL,
                    qty=qty,
                    price=fill_price,
                    quote_qty=qty * fill_price,
                    fee=Decimal("0"),
                    fee_asset="USDT",
                    liquidity=Liquidity.TAKER,  # симуляция немедленного исполнения
                    is_maker=False,
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    trade_id=str(ts_ms),
                    strategy_tag=strategy_tag,
                    executed_at=executed_at,
                )
                session.add(fill)

                self._upsert_position_row(session, symbol)

                # ---- PnL ledger for realized PnL on SELL ----
                if side == "SELL" and prev_qty > 0:
                    close_qty = min(qty, prev_qty)
                    if close_qty > 0:
                        pnl_usd = (fill_price - (prev_avg_for_pnl or prev_avg)) * close_qty
                        base, quote = _split_symbol(symbol)
                        ex = getattr(settings, "active_provider", None) or "PAPER"
                        acc = "paper"
                        price_usd = Decimal("1") if _is_usd_quote(quote) else None

                        self._pnl.log_trade_realized(
                            session,
                            ts=executed_at,
                            exchange=str(ex),
                            account_id=acc,
                            symbol=symbol,
                            base_asset=base,
                            quote_asset=quote,
                            realized_asset=pnl_usd,
                            realized_usd=pnl_usd,
                            price_usd=price_usd,
                            ref_order_id=str(order.id),
                            ref_trade_id=str(ts_ms),
                            meta={
                                "meta_ver": 1,
                                "mode": "paper",
                                "side": side,
                                "qty": float(qty),
                                "price": float(fill_price),
                                "fee": 0.0,
                                "fee_asset": "USDT",
                                "client_order_id": client_order_id,
                                "exchange_order_id": None,
                                "trade_id": str(ts_ms),
                                "strategy_tag": strategy_tag,
                            },
                            emit_sse=True,
                        )

                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        return client_order_id

    async def _apply_memory_fill(
        self, symbol: str, side: str, price: Decimal, qty: Decimal, ts_ms: int
    ) -> None:
        async with self._lock:
            mp = self._positions.setdefault(symbol, MemPosition(symbol=symbol))
            if side == "BUY":
                new_qty = mp.qty + qty
                if new_qty > 0:
                    mp.avg_price = (
                        (mp.avg_price * mp.qty + price * qty) / new_qty if mp.qty > 0 else price
                    )
                    mp.qty = new_qty
                mp.ts_ms = ts_ms
            else:  # SELL
                if mp.qty > 0:
                    close_qty = min(qty, mp.qty)
                    if close_qty > 0:
                        pnl = (price - mp.avg_price) * close_qty
                        mp.realized_pnl += pnl
                        mp.qty -= close_qty
                        if mp.qty <= Decimal("0.000000000001"):
                            mp.qty = Decimal("0")
                            mp.avg_price = Decimal("0")
                        mp.ts_ms = ts_ms
                # ignore opening shorts in paper engine

    def _upsert_position_row(self, session: Session, symbol: str) -> None:
        mp = self._positions.get(symbol) or MemPosition(symbol=symbol)

        pos_row: Optional[Position] = (
            session.query(Position)
            .filter(
                Position.workspace_id == self._wsid,
                Position.symbol == symbol,
                Position.side == PositionSide.BUY,
                Position.is_open == True,  # noqa: E712
            )
            .order_by(Position.id.desc())
            .first()
        )

        if mp.qty > 0:
            if pos_row is None:
                pos_row = Position(
                    workspace_id=self._wsid,
                    symbol=symbol,
                    side=PositionSide.BUY,
                    qty=mp.qty,
                    entry_price=mp.avg_price,
                    last_mark_price=None,
                    realized_pnl=mp.realized_pnl,
                    unrealized_pnl=None,
                    status=PositionStatus.OPEN,
                    is_open=True,
                    note="paper",
                )
                session.add(pos_row)
            else:
                pos_row.qty = mp.qty
                pos_row.entry_price = mp.avg_price
                pos_row.realized_pnl = mp.realized_pnl
                pos_row.is_open = True
                pos_row.status = PositionStatus.OPEN
                pos_row.closed_at = None
        else:
            if pos_row is not None:
                pos_row.is_open = False
                pos_row.status = PositionStatus.CLOSED
                pos_row.closed_at = datetime.utcnow()
                pos_row.qty = Decimal("0")
                pos_row.entry_price = Decimal("0")
                pos_row.unrealized_pnl = Decimal("0")
