# app/execution/paper_executor.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal, getcontext
from typing import Callable, Dict, Optional, Protocol

from app.services import book_tracker as bt_service
from app.models.orders import Order, OrderSide, OrderType, OrderStatus, TimeInForce
from app.models.fills import Fill, FillSide, Liquidity
from app.models.positions import Position, PositionSide, PositionStatus

# Use sane precision for price/qty math (matches Numeric(28,12))
getcontext().prec = 34


@dataclass
class MemPosition:
    symbol: str
    qty: Decimal = Decimal("0")        # >0 long, <0 short (we only open long in paper)
    avg_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    ts_ms: int = 0


class SessionFactory(Protocol):
    """A Protocol so any callable returning a SQLAlchemy Session fits."""
    def __call__(self):
        ...


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dec(x: float | str | Decimal | None) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


class PaperExecutor:
    """
    Paper execution simulator with DB persistence:
      - place_maker("BUY") fills at best bid; "SELL" fills at best ask (instant fill).
      - flatten_symbol() sells any open long at best ask.
      - cancel_orders() is a no-op (no queue modeled).
    Persistence:
      - Writes Order (status=FILLED), Fill, and updates/creates Position.
    """

    def __init__(self, session_factory: Optional[SessionFactory] = None, workspace_id: int = 1) -> None:
        self._lock = asyncio.Lock()
        self._positions: Dict[str, MemPosition] = {}
        self._session_factory = session_factory  # e.g., from app.models.base import SessionLocal
        self._wsid = workspace_id

    # -------- StrategyEngine interface --------

    async def start_symbol(self, symbol: str) -> None:
        # No prep needed in paper mode; keep placeholder for parity with live
        return None

    async def stop_symbol(self, symbol: str) -> None:
        # No teardown needed in paper mode
        return None

    async def flatten_symbol(self, symbol: str) -> None:
        """Close any open long at best available price and persist the trade."""
        sym = symbol.upper()

        # quick check without holding the lock long
        async with self._lock:
            pos = self._positions.get(sym)
            if not pos or pos.qty <= Decimal("0"):
                return
            close_qty = pos.qty

        # get a fresh quote
        q = await bt_service.get_quote(sym)
        bid = _dec(q.get("bid", 0.0))
        ask = _dec(q.get("ask", 0.0))
        exit_px = ask or bid or pos.avg_price or Decimal("0")

        # persist SELL order/ fill / position
        await self._fill_and_persist(
            symbol=sym,
            side="SELL",
            fill_price=exit_px,
            qty=close_qty,
            strategy_tag="flatten",
        )

        # mirror in-memory state (under lock)
        async with self._lock:
            # realized already accounted in _fill_and_persist via in-memory call there
            mp = self._positions.setdefault(sym, MemPosition(symbol=sym))
            mp.qty = Decimal("0")
            mp.avg_price = Decimal("0")
            mp.ts_ms = _now_ms()

    async def cancel_orders(self, symbol: str) -> None:
        # No ad-hoc order queue in this paper engine
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
        Instant fill simulation:
          - BUY fills at current best bid if present, otherwise at provided price.
          - SELL fills at current best ask if present, otherwise at provided price.
        Returns a pseudo client_order_id.
        """
        sym = symbol.upper()
        s_up = side.upper().strip()
        qty_dec = _dec(qty)
        if qty_dec <= 0:
            return None

        # fetch current quote once
        q = await bt_service.get_quote(sym)
        bid = _dec(q.get("bid", 0.0))
        ask = _dec(q.get("ask", 0.0))

        # choose fill price conservatively
        fill_price = _dec(price)
        if s_up == "BUY" and bid > 0:
            fill_price = bid
        elif s_up == "SELL" and ask > 0:
            fill_price = ask

        # persist + update memory
        coid = await self._fill_and_persist(
            symbol=sym,
            side=s_up,
            fill_price=fill_price,
            qty=qty_dec,
            strategy_tag=tag,
        )
        return coid

    async def get_position(self, symbol: str) -> dict:
        sym = symbol.upper()
        # snapshot under lock
        async with self._lock:
            pos = self._positions.get(sym) or MemPosition(symbol=sym)
            snap_qty = pos.qty
            snap_avg = pos.avg_price
            snap_real = pos.realized_pnl
            snap_ts = pos.ts_ms

        # compute uPnL outside the lock
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

    async def _fill_and_persist(
        self,
        symbol: str,
        side: str,
        fill_price: Decimal,
        qty: Decimal,
        strategy_tag: str,
    ) -> str:
        """
        Creates a FILLED order + a single fill row and updates the Position
        (both in DB and in-memory). Returns client_order_id.
        """
        ts_ms = _now_ms()
        client_order_id = f"paper-{symbol}-{ts_ms}"

        # Update in-memory first (under lock) to keep behavior identical to your original engine
        await self._apply_memory_fill(symbol, side, fill_price, qty, ts_ms)

        # Persist if we have a session factory
        if self._session_factory:
            from sqlalchemy.orm import Session  # local import to avoid hard dep at import time
            session: Session
            session = self._session_factory()
            try:
                # 1) Order (already filled)
                order = Order(
                    workspace_id=self._wsid,
                    symbol=symbol,
                    side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                    type=OrderType.MARKET,  # market-like fill in paper
                    tif=TimeInForce.IOC,
                    qty=qty,
                    price=None if side == "BUY" else None,  # not meaningful for MARKET
                    filled_qty=qty,
                    avg_fill_price=fill_price,
                    status=OrderStatus.FILLED,
                    is_active=False,
                    strategy_tag=strategy_tag,
                    client_order_id=client_order_id,
                )
                session.add(order)
                session.flush()  # get order.id

                # 2) Fill
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
                    liquidity=Liquidity.TAKER,
                    is_maker=False,
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    trade_id=str(ts_ms),
                    strategy_tag=strategy_tag,
                    executed_at=None,  # defaults to now()
                )
                session.add(fill)

                # 3) Position upsert/update
                self._upsert_position_row(session, symbol)

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
                        (mp.avg_price * mp.qty + price * qty) / new_qty
                        if mp.qty > 0 else price
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

    def _upsert_position_row(self, session, symbol: str) -> None:
        """
        Sync the DB Position row with the in-memory snapshot (simple write-through).
        We keep ONE row per (workspace_id, symbol, side=BUY) while open; on zero qty we mark CLOSED.
        """
        mp = self._positions.get(symbol) or MemPosition(symbol=symbol)

        # Try fetch an open row
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
            # Upsert open position
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
            # allow DB to set updated_at automatically
        else:
            # No quantity -> close any open row
            if pos_row is not None:
                pos_row.is_open = False
                pos_row.status = PositionStatus.CLOSED
                pos_row.closed_at = None  # let DB default set if you prefer; else set func.now()
                pos_row.qty = Decimal("0")
                pos_row.entry_price = Decimal("0")

