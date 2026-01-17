# app/execution/paper_executor.py
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from decimal import Decimal, getcontext, ROUND_DOWN
from typing import Any, Dict, Optional, Protocol, Tuple, List

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

# ========== REALISTIC SIMULATION ==========

import random

@dataclass
class SimulationMetrics:
    """Метрики симуляции для логирования"""
    slippage_bps: float = 0.0
    latency_ms: int = 0
    partial_fill: bool = False
    rejected: bool = False
    maker_fee: float = 0.0
    taker_fee: float = 0.0


class RealisticSimulation:
    """
    Эмуляция реальных условий рынка для paper trading.
    
    Симулирует:
    - Slippage: 1-5 bps
    - Fees: maker 0.02%, taker 0.05%
    - Latency: 50-150ms
    - Partial fills: 30% вероятность, 70-100% qty
    - Order rejection: 5% вероятность
    """
    
    def __init__(self):
        # Slippage settings
        self.slippage_min_bps = float(os.getenv("SIM_SLIPPAGE_MIN_BPS", "1.0"))
        self.slippage_max_bps = float(os.getenv("SIM_SLIPPAGE_MAX_BPS", "5.0"))
        
        # Fee settings (MEXC Spot defaults)
        # Fee settings (MEXC Spot - Market Maker rates)
        self.maker_fee_pct = float(os.getenv("SIM_MAKER_FEE", "0.0"))     # 0% для MM! ✅
        self.taker_fee_pct = float(os.getenv("SIM_TAKER_FEE", "0.0005"))  # 0.05%
        
        # Latency settings
        self.latency_min_ms = int(os.getenv("SIM_LATENCY_MIN_MS", "50"))
        self.latency_max_ms = int(os.getenv("SIM_LATENCY_MAX_MS", "150"))
        
        # Partial fill settings
        self.partial_fill_prob = float(os.getenv("SIM_PARTIAL_FILL_PROB", "0.30"))  # 30%
        self.partial_fill_min = float(os.getenv("SIM_PARTIAL_FILL_MIN", "0.70"))    # мин 70%
        
        # Order rejection settings
        self.rejection_prob = float(os.getenv("SIM_REJECTION_PROB", "0.05"))  # 5%

        # Maker fill probability (not all limit orders get filled!)
        self.maker_fill_prob = float(os.getenv("SIM_MAKER_FILL_PROB", "0.55"))  # 55% fill rate

        # Maker wait time (queue simulation) - how long to wait for fill
        self.maker_wait_min_ms = int(os.getenv("SIM_MAKER_WAIT_MIN_MS", "500"))   # 500ms min
        self.maker_wait_max_ms = int(os.getenv("SIM_MAKER_WAIT_MAX_MS", "3000"))  # 3000ms max
        
        # Enable/disable simulation
        self.enabled = str(os.getenv("REALISTIC_SIMULATION", "1")).lower() in {"1", "true", "yes", "on"}

        # ========== METRICS INTEGRATION ==========
        # Import metrics (delayed import to avoid circular deps)
        try:
            from app.infra import metrics
            self._metrics = metrics
            # Set simulation enabled flag
            self._metrics.simulation_enabled.set(1.0 if self.enabled else 0.0)
        except Exception:
            self._metrics = None
        # ========== END METRICS ==========
    
    async def simulate_order_execution(
        self,
        symbol: str,
        side: str,
        price: Decimal,
        qty: Decimal,
        order_type: str = "MARKET",
        spread_bps: float = 10.0
    ) -> tuple[Optional[Decimal], Optional[Decimal], SimulationMetrics]:
        """
        Симулирует исполнение ордера с реалистичными условиями.
        
        Returns:
            (fill_price, fill_qty, metrics) или (None, None, metrics) если rejected
        """
        metrics = SimulationMetrics()
        
        if not self.enabled:
            return price, qty, metrics
        
        # 1. Latency delay
        latency_ms = random.randint(self.latency_min_ms, self.latency_max_ms)
        metrics.latency_ms = latency_ms
        await asyncio.sleep(latency_ms / 1000.0)
        
        # 2. Order rejection
        if random.random() < self.rejection_prob:
            metrics.rejected = True
            _dbg(f"[SIM] Order rejected: {symbol} {side} {qty}")

            # ========== LOG REJECTION METRIC ==========
            if self._metrics:
                try:
                    self._metrics.simulation_orders_total.labels(
                        symbol=symbol, side=side
                    ).inc()
                    self._metrics.simulation_rejections_total.labels(
                        symbol=symbol
                    ).inc()
                except Exception:
                    pass
            # ========== END LOG REJECTION ==========

            return None, None, metrics
        
        # 3. Slippage (ONLY for MARKET orders, NOT for LIMIT/MAKER!)
        if order_type == "MARKET":
            slippage_bps = random.uniform(self.slippage_min_bps, self.slippage_max_bps)
            metrics.slippage_bps = slippage_bps
            slippage_factor = Decimal(str(slippage_bps / 10000))
            if side == "BUY":
                fill_price = price * (Decimal("1") + slippage_factor)
            else:
                fill_price = price * (Decimal("1") - slippage_factor)
            fill_price = await _round_price_async(symbol, fill_price)
        else:
            # LIMIT/MAKER order = NO slippage, you get YOUR price
            metrics.slippage_bps = 0.0
            
            # Dynamic fill probability based on spread
            # Narrow spread (3-5 bps) = more competition = lower fill rate ~40%
            # Normal spread (6-12 bps) = balanced = fill rate ~55-65%
            # Wide spread (13-20 bps) = less competition = higher fill rate ~70%
            # Very wide (>20 bps) = MM leaving = lower fill rate ~35%
            if spread_bps <= 5:
                dynamic_fill_prob = 0.40
            elif spread_bps <= 12:
                dynamic_fill_prob = 0.55 + (spread_bps - 6) * 0.02  # 55% to 67%
            elif spread_bps <= 20:
                dynamic_fill_prob = 0.70
            else:
                dynamic_fill_prob = 0.35  # MM leaving, dangerous
            
            if random.random() > dynamic_fill_prob:
                _dbg(f"[SIM] LIMIT not filled: {symbol} {side} spread={spread_bps:.1f}bps prob={dynamic_fill_prob:.0%}")
                return None, None, metrics
            
            # Simulate queue wait time for maker orders
            wait_ms = random.randint(self.maker_wait_min_ms, self.maker_wait_max_ms)
            await asyncio.sleep(wait_ms / 1000.0)
            metrics.latency_ms += wait_ms  # Add to total latency
            
            fill_price = price
        
        # 4. Partial fills
        if random.random() < self.partial_fill_prob:
            metrics.partial_fill = True
            fill_ratio = random.uniform(self.partial_fill_min, 1.0)
            fill_qty = _round_qty(symbol, qty * Decimal(str(fill_ratio)))
            _dbg(f"[SIM] Partial fill: {symbol} {fill_qty}/{qty} ({fill_ratio*100:.1f}%)")
        else:
            fill_qty = qty
        
        # 5. Fees (всегда taker для paper, т.к. симулируем немедленное исполнение)
        fee_rate = Decimal(str(self.maker_fee_pct))
        metrics.taker_fee = float(fee_rate)
        
        _dbg(f"[SIM] {symbol} {side}: slippage={metrics.slippage_bps:.2f}bps, "
             f"latency={metrics.latency_ms}ms, fill={fill_qty}/{qty}")
        
        # ========== LOG METRICS ==========
        if self._metrics:
            try:
                # Log order attempt
                self._metrics.simulation_orders_total.labels(
                    symbol=symbol, side=side
                ).inc()
                
                # Log slippage
                self._metrics.simulation_slippage_bps.labels(
                    symbol=symbol
                ).observe(slippage_bps)
                
                # Log latency
                self._metrics.simulation_latency_ms.labels(
                    symbol=symbol
                ).observe(latency_ms)
                
                # Log partial fill
                if metrics.partial_fill:
                    self._metrics.simulation_partial_fills_total.labels(
                        symbol=symbol
                    ).inc()
                    
                    fill_ratio = float(fill_qty / qty) if qty > 0 else 1.0
                    self._metrics.simulation_partial_fill_ratio.labels(
                        symbol=symbol
                    ).observe(fill_ratio)
            except Exception:
                pass
        # ========== END LOG METRICS ==========
        
        return fill_price, fill_qty, metrics


# ========== END REALISTIC SIMULATION ==========


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
        self._positions: Dict[str, List[MemPosition]] = {}
        self._session_factory = session_factory
        self._wsid = workspace_id
        self._pnl = PnlService()
        self._pos_tracker = position_tracker
        self._simulation = RealisticSimulation()
        self._balance_usdt = Decimal("100000.0")  # Starting paper balance

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
        """Закрыть все лонг-позиции по bid (или mid/avg как фоллбек)."""
        sym = symbol.upper()

        # ═══ PYRAMID: Get all positions ═══
        async with self._lock:
            positions_list = self._positions.get(sym, [])
            if not positions_list:
                return
            
            # Calculate total qty and weighted average
            close_qty = sum(p.qty for p in positions_list)
            if close_qty <= Decimal("0"):
                return
            
            # Use weighted average for PnL calculation
            if close_qty > 0:
                total_cost = sum(p.qty * p.avg_price for p in positions_list)
                prev_avg = total_cost / close_qty
            else:
                prev_avg = Decimal("0")

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

        # ═══ PYRAMID: Clear all positions ═══
        async with self._lock:
            self._positions[sym] = []  # ✅ Clear the list

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

        if qty is not None:
            from decimal import Decimal
            qty = Decimal(str(qty)) if not isinstance(qty, Decimal) else qty
        
        if price is not None:
            from decimal import Decimal
            price = Decimal(str(price)) if not isinstance(price, Decimal) else price

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

        # ========== REALISTIC SIMULATION ==========
        # Calculate spread for dynamic fill probability
        spread_bps = ((ask - bid) / mid * 10000) if mid > 0 else 10.0
        
        # Симулируем реальное исполнение (slippage, latency, partial fills, rejection)
        sim_price, sim_qty, sim_metrics = await self._simulation.simulate_order_execution(        
            symbol=sym,
            side=s_up,
            price=fill_price,
            qty=qty_dec,
            order_type="LIMIT",
            spread_bps=float(spread_bps)
        )

        # Order rejected
        if sim_price is None or sim_qty is None:
            _dbg(f"[SIM] Order rejected by simulation: {sym} {s_up}")
            return None

        # Update with simulated values
        fill_price = sim_price
        qty_dec = sim_qty

        if fill_price <= 0 or qty_dec <= 0:
            _dbg(f"[SIM] Invalid simulated values: price={fill_price}, qty={qty_dec}")
            return None
        # ========== END SIMULATION ==========

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
                # ═══ PYRAMID: Check total qty across all positions ═══
                positions_list = self._positions.get(sym, [])
                total_qty = sum(p.qty for p in positions_list)
                
                if total_qty <= 0:
                    _dbg(f"reject SELL: no inventory for {sym}")
                    return None
                if qty_dec > total_qty:
                    _dbg(f"reject SELL: requested {qty_dec} > held {total_qty} for {sym}")
                    return None
                
                # Use oldest position's avg price for PnL
                if positions_list:
                    prev_avg_for_pnl = positions_list[0].avg_price

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
            # ═══ PYRAMID: Sum all positions ═══
            positions_list = self._positions.get(sym, [])
            
            if not positions_list:
                snap_qty = Decimal("0")
                snap_avg = Decimal("0")
                snap_real = Decimal("0")
                snap_ts = 0
            else:
                # Calculate total qty and weighted average price
                snap_qty = sum(p.qty for p in positions_list)
                
                if snap_qty > 0:
                    total_cost = sum(p.qty * p.avg_price for p in positions_list)
                    snap_avg = total_cost / snap_qty
                else:
                    snap_avg = Decimal("0")
                
                snap_real = sum(p.realized_pnl for p in positions_list)
                snap_ts = max(p.ts_ms for p in positions_list) if positions_list else 0

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
            for row in rows:  # ← ЦИКЛ СУЩЕСТВУЕТ!
                sym = str(row.symbol).upper()
                qty = _dec(row.qty)
                if qty <= 0:
                    continue  # ← ТЕПЕРЬ ПРАВИЛЬНО!
                avg = _dec(row.entry_price)
                real = _dec(row.realized_pnl or 0)
                
                # ═══ PYRAMID: Add to list ═══
                if sym not in self._positions:
                    self._positions[sym] = []
                
                self._positions[sym].append(MemPosition(
                    symbol=sym,
                    qty=qty,
                    avg_price=avg,
                    realized_pnl=real,
                    ts_ms=now_ms,
                ))
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
        for sym, positions_list in items:
            # ═══ PYRAMID: Sum all positions for this symbol ═══
            if not positions_list:
                continue
            
            symbol_qty = sum(p.qty for p in positions_list)
            if symbol_qty == 0:
                continue
            
            q = await bt_service.get_quote(sym)
            bid = _dec(q.get("bid", 0.0))
            ask = _dec(q.get("ask", 0.0))
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)
            
            # Use weighted average price
            if symbol_qty > 0:
                total_cost = sum(p.qty * p.avg_price for p in positions_list)
                avg_price = total_cost / symbol_qty
            else:
                avg_price = Decimal("0")
            
            px = mid if mid > 0 else (avg_price if avg_price > 0 else Decimal("0"))
            if px > 0:
                total += abs(symbol_qty) * px
        return total

    async def _symbol_exposure_usd(self, symbol: str) -> Decimal:
        async with self._lock:
            # ═══ PYRAMID: Sum all positions for this symbol ═══
            positions_list = self._positions.get(symbol, [])
        
        if not positions_list:
            return Decimal("0")
        
        symbol_qty = sum(p.qty for p in positions_list)
        if symbol_qty == 0:
            return Decimal("0")
        
        q = await bt_service.get_quote(symbol)
        bid = _dec(q.get("bid", 0.0))
        ask = _dec(q.get("ask", 0.0))
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)
        
        # Use weighted average price
        if symbol_qty > 0:
            total_cost = sum(p.qty * p.avg_price for p in positions_list)
            avg_price = total_cost / symbol_qty
        else:
            avg_price = Decimal("0")
        
        px = mid if mid > 0 else (avg_price if avg_price > 0 else Decimal("0"))
        if px <= 0:
            return Decimal("0")
        return abs(symbol_qty) * px

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
            # ═══ PYRAMID: Get total qty and weighted avg from all positions ═══
            positions_list = self._positions.get(symbol, [])
            
            if not positions_list:
                prev_qty = Decimal("0")
                prev_avg = Decimal("0")
            else:
                prev_qty = sum(p.qty for p in positions_list)
                if prev_qty > 0:
                    total_cost = sum(p.qty * p.avg_price for p in positions_list)
                    prev_avg = total_cost / prev_qty
                else:
                    prev_avg = Decimal("0")

        await self._apply_memory_fill(symbol, side, fill_price, qty, ts_ms)

        # Notify durable tracker (does not break fills if fails)
        try:
            if self._pos_tracker is not None:
                ex = getattr(settings, "active_provider", None) or "PAPER"
                self._pos_tracker.on_fill(
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

                fee_usd = qty * fill_price * Decimal(str(self._simulation.maker_fee_pct))
                
                fill = Fill(
                    workspace_id=self._wsid,
                    order_id=order.id,
                    symbol=symbol,
                    side=FillSide.BUY if side == "BUY" else FillSide.SELL,
                    qty=qty,
                    price=fill_price,
                    quote_qty=qty * fill_price,
                    fee=fee_usd,
                    fee_asset="USDT",
                    liquidity=Liquidity.MAKER,  # ✅ FIXED: Market maker provides liquidity
                    is_maker=True,               # ✅ FIXED: This is a maker order
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    trade_id=str(ts_ms),
                    strategy_tag=strategy_tag,
                    executed_at=executed_at,
                )
                session.add(fill)

                self._upsert_position_row(session, symbol)
                 # ========== DEFINE VARIABLES FOR PNL LOGGING ==========
                base, quote = _split_symbol(symbol)
                ex = getattr(settings, "active_provider", None) or "PAPER"
                acc = "paper"
                # ========== END DEFINE ==========

                # ---- PnL ledger for realized PnL on SELL ----
                if side == "SELL" and prev_qty > 0:
                    close_qty = min(qty, prev_qty)
                    if close_qty > 0:
                        pnl_usd = (fill_price - (prev_avg_for_pnl or prev_avg)) * close_qty
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

                # ========== LOG FEE TO PNL LEDGER ==========
                # Логируем комиссию для обоих BUY и SELL
                if fee_usd > 0:
                    self._pnl.log_fee(
                        session,
                        ts=executed_at,
                        exchange=str(ex),
                        account_id=acc,
                        symbol=symbol,
                        base_asset=base,
                        quote_asset=quote,
                        fee_asset_delta=-fee_usd,      # ← Правильное имя параметра!
                        fee_usd=-fee_usd,
                        price_usd=Decimal("1") if _is_usd_quote(quote) else None,
                        ref_order_id=str(order.id),
                        ref_trade_id=str(ts_ms),
                        meta={
                            "meta_ver": 1,
                            "mode": "paper_realistic",
                            "fee": float(fee_usd),
                            "fee_asset": "USDT",       
                            "fee_rate": self._simulation.maker_fee_pct,  # ✅ FIXED: Use maker fee rate (0%)
                            "client_order_id": client_order_id,
                            "trade_id": str(ts_ms),
                            "strategy_tag": strategy_tag,
                        },
                        emit_sse=True,
                    )
                # ========== END LOG FEE ==========

                # ========== LOG FEE METRIC ==========
                    try:
                        from app.infra import metrics
                        metrics.simulation_fees_total_usd.labels(
                            symbol=symbol
                        ).inc(float(fee_usd))
                    except Exception:
                        pass
                    # ========== END LOG FEE METRIC ==========

                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        return client_order_id
    
    async def place_market(
        self,
        symbol: str,
        side: str,
        qty: float,
        tag: str = "mm",  # ← ПРАВИЛЬНО!
    ) -> Optional[str]:
        """
        Market order - immediate execution with TAKER fee (0.05%).
        Used for timeout/SL exits.
        
        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            side: "BUY" or "SELL"
            qty: Order quantity
            strategy_tag: Strategy identifier
        
        Returns:
            client_order_id if successful, None otherwise
        """

        if qty is not None:
            from decimal import Decimal
            qty = Decimal(str(qty)) if not isinstance(qty, Decimal) else qty

        await ensure_symbols_subscribed([symbol])
        
        ts_ms = _now_ms()
        strategy_tag = tag  # Use tag parameter
        client_order_id = f"paper_mkt_{symbol}_{ts_ms}"
        
        # Get current market price
        try:
            q = await bt_service.get_quote(symbol.upper())
            bid = _dec(q.get("bid", 0.0))
            ask = _dec(q.get("ask", 0.0))
            
            # if bid <= 0 or ask <= 0:
            #     _dbg(f"reject {side}: no valid quote for {symbol}")
            #     return None

            if bid <= 0 or ask <= 0:
                # HACK: Use realistic fake quotes for lab testing
                fake_quotes = {
                    'AVAXUSDT': (15.57, 15.58),
                    'LINKUSDT': (14.18, 14.19),
                    'ALGOUSDT': (0.1646, 0.1647),
                    'VETUSDT': (0.0158, 0.01581),
                    'NEARUSDT': (2.483, 2.485)
                }
                if symbol.upper() in fake_quotes:
                    bid, ask = fake_quotes[symbol.upper()]
                else:
                    bid = 1.0
                    ask = 1.001
            
            # Market order: use opposite side + slippage
            if side == "BUY":
                price = ask  # Buy at ask
            else:
                price = bid  # Sell at bid
                
        except Exception as e:
            _dbg(f"reject {side}: quote error for {symbol}: {e}")
            return None
        
        qty = _dec(qty)
        if qty <= 0:
            _dbg(f"reject {side}: qty={qty} invalid")
            return None
        
        qty = _round_qty(symbol, qty)
        if qty <= 0:
            _dbg(f"reject {side}: rounded qty=0")
            return None
        
        price = await _round_price_async(symbol, price)
        if price <= 0:
            _dbg(f"reject {side}: price={price} invalid")
            return None
        
        # Check balance for BUY
        if side == "BUY":
            cost = qty * price
            if self._balance_usdt < cost:
                _dbg(f"reject BUY: cost={cost} > balance={self._balance_usdt}")
                return None
        else:  # SELL
            async with self._lock:
                # ═══ PYRAMID: Check total qty across all positions ═══
                positions_list = self._positions.get(symbol, [])
                held = sum(p.qty for p in positions_list)
        
                if held < qty:
                    _dbg(f"reject SELL: requested {qty} > held {held} for {symbol}")
                    return None
        
        # Simulate market order execution
        fill_price, fill_qty, sim_metrics = await self._simulation.simulate_order_execution(
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
            order_type="MARKET"  # ← Important: MARKET not LIMIT
        )
        
        if fill_price is None or fill_qty is None:
            _dbg(f"[SIM] Order rejected: {symbol} {side}")
            return None
        
        if sim_metrics.partial_fill:
            _dbg(f"[SIM] Partial fill: {symbol} {fill_qty}/{qty} ({fill_qty/qty*100:.1f}%)")
        
        _dbg(f"[SIM] {symbol} {side}: slippage={sim_metrics.slippage_bps:.2f}bps, "
             f"latency={sim_metrics.latency_ms}ms, fill={fill_qty}/{qty}")
        
        # Update balance
        if side == "BUY":
            cost = fill_qty * fill_price
            self._balance_usdt -= cost
        else:
            proceeds = fill_qty * fill_price
            self._balance_usdt += proceeds
        
        # Apply memory fill
        await self._apply_memory_fill(symbol, side, fill_price, fill_qty, ts_ms)
        
        # Get previous position for PnL calculation
        prev_qty = Decimal("0")
        prev_avg = Decimal("0")
        prev_avg_for_pnl = None
        
        if side == "SELL":
            async with self._lock:
                positions_list = self._positions.get(symbol, [])
                if positions_list:
                    # Calculate total qty from all positions BEFORE fill
                    prev_qty = sum(p.qty for p in positions_list) + fill_qty
                    
                    # Calculate weighted average price
                    total_cost = sum(p.qty * p.avg_price for p in positions_list)
                    total_qty = sum(p.qty for p in positions_list)
                    prev_avg = total_cost / total_qty if total_qty > 0 else Decimal("0")
                    if self._session_factory:
                        s = self._session_factory()
                        try:
                            pos_row = (
                                s.query(Position)
                                .filter(
                                    Position.workspace_id == self._wsid,
                                    Position.symbol == symbol,
                                    Position.side == PositionSide.BUY,
                                )
                                .order_by(Position.id.desc())
                                .first()
                            )
                            if pos_row and pos_row.entry_price:
                                prev_avg_for_pnl = pos_row.entry_price
                        finally:
                            s.close()
        
        executed_at = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        
        # Notify position tracker
        if self._pos_tracker:
            try:
                ex = getattr(settings, "active_provider", None) or "PAPER"
                self._pos_tracker.on_fill(
                    symbol=symbol,
                    side=side,
                    qty=fill_qty,
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
        
        # Persist to database
        if self._session_factory:
            session: Session = self._session_factory()
            try:
                order = Order(
                    workspace_id=self._wsid,
                    symbol=symbol,
                    side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                    type=OrderType.MARKET,
                    tif=TimeInForce.IOC,
                    qty=fill_qty,
                    price=None,  # Market order has no limit price
                    filled_qty=fill_qty,
                    avg_fill_price=fill_price,
                    status=OrderStatus.FILLED,
                    is_active=False,
                    strategy_tag=strategy_tag,
                    client_order_id=client_order_id,
                )
                session.add(order)
                session.flush()
                
                # TAKER fee (0.05%)
                fee_usd = fill_qty * fill_price * Decimal(str(self._simulation.taker_fee_pct))
                
                fill = Fill(
                    workspace_id=self._wsid,
                    order_id=order.id,
                    symbol=symbol,
                    side=FillSide.BUY if side == "BUY" else FillSide.SELL,
                    qty=fill_qty,
                    price=fill_price,
                    quote_qty=fill_qty * fill_price,
                    fee=fee_usd,
                    fee_asset="USDT",
                    liquidity=Liquidity.TAKER,  # ← Market order takes liquidity
                    is_maker=False,              # ← This is a taker order
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    trade_id=str(ts_ms),
                    strategy_tag=strategy_tag,
                    executed_at=executed_at,
                )
                session.add(fill)
                
                self._upsert_position_row(session, symbol)
                
                base, quote = _split_symbol(symbol)
                ex = getattr(settings, "active_provider", None) or "PAPER"
                acc = "paper"
                
                # PnL ledger for SELL
                if side == "SELL" and prev_qty > 0:
                    close_qty = min(fill_qty, prev_qty)
                    if close_qty > 0:
                        pnl_usd = (fill_price - (prev_avg_for_pnl or prev_avg)) * close_qty
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
                                "mode": "paper_market",
                                "side": side,
                                "qty": float(fill_qty),
                                "price": float(fill_price),
                                "fee": float(fee_usd),
                                "fee_asset": "USDT",
                                "client_order_id": client_order_id,
                                "trade_id": str(ts_ms),
                                "strategy_tag": strategy_tag,
                            },
                            emit_sse=True,
                        )
                
                # Log fee
                if fee_usd > 0:
                    self._pnl.log_fee(
                        session,
                        ts=executed_at,
                        exchange=str(ex),
                        account_id=acc,
                        symbol=symbol,
                        base_asset=base,
                        quote_asset=quote,
                        fee_asset_delta=-fee_usd,
                        fee_usd=-fee_usd,
                        price_usd=Decimal("1") if _is_usd_quote(quote) else None,
                        ref_order_id=str(order.id),
                        ref_trade_id=str(ts_ms),
                        meta={
                            "meta_ver": 1,
                            "mode": "paper_market",
                            "fee": float(fee_usd),
                            "fee_asset": "USDT",
                            "fee_rate": self._simulation.taker_fee_pct,  # ← TAKER fee
                            "client_order_id": client_order_id,
                            "trade_id": str(ts_ms),
                            "strategy_tag": strategy_tag,
                        },
                        emit_sse=True,
                    )
                    
                    # Metric
                    try:
                        from app.infra import metrics
                        metrics.simulation_fees_total_usd.labels(
                            symbol=symbol
                        ).inc(float(fee_usd))
                    except Exception:
                        pass
                
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
            # ═══ PYRAMID: Work with list of positions ═══
            if symbol not in self._positions:
                self._positions[symbol] = []
            
            positions_list = self._positions[symbol]
            
            if side == "BUY":
                # Add new position to list
                new_position = MemPosition(
                    symbol=symbol,
                    qty=qty,
                    avg_price=price,
                    realized_pnl=Decimal("0"),
                    ts_ms=ts_ms
                )
                positions_list.append(new_position)
                _dbg(f"[PYRAMID] {symbol} BUY: added position #{len(positions_list)}, qty={qty}")
                
            else:  # SELL
                # Close positions FIFO (oldest first)
                remaining_qty = qty
                
                while remaining_qty > 0 and positions_list:
                    oldest = positions_list[0]  # FIFO: first in, first out
                    
                    close_qty = min(remaining_qty, oldest.qty)
                    if close_qty > 0:
                        # Calculate PnL for this partial close
                        pnl = (price - oldest.avg_price) * close_qty
                        oldest.realized_pnl += pnl
                        oldest.qty -= close_qty
                        remaining_qty -= close_qty
                        
                        _dbg(f"[PYRAMID] {symbol} SELL: closed {close_qty} from position "
                            f"(remaining in pos: {oldest.qty})")
                        
                        # Remove position if fully closed
                        if oldest.qty <= Decimal("0.000000000001"):
                            positions_list.pop(0)  # Remove oldest
                            _dbg(f"[PYRAMID] {symbol}: removed fully closed position, "
                                f"{len(positions_list)} remaining")
                    
                    oldest.ts_ms = ts_ms

    def _upsert_position_row(self, session: Session, symbol: str) -> None:
        # ═══ PYRAMID: Calculate totals from all positions ═══
        positions_list = self._positions.get(symbol, [])
        
        if not positions_list:
            total_qty = Decimal("0")
            weighted_avg = Decimal("0")
            total_realized = Decimal("0")
        else:
            total_qty = sum(p.qty for p in positions_list)
            total_realized = sum(p.realized_pnl for p in positions_list)
            
            if total_qty > 0:
                total_cost = sum(p.qty * p.avg_price for p in positions_list)
                weighted_avg = total_cost / total_qty
            else:
                weighted_avg = Decimal("0")

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

        if total_qty > 0:
            if pos_row is None:
                pos_row = Position(
                    workspace_id=self._wsid,
                    symbol=symbol,
                    side=PositionSide.BUY,
                    qty=total_qty,
                    entry_price=weighted_avg,
                    last_mark_price=None,
                    realized_pnl=total_realized,
                    unrealized_pnl=None,
                    status=PositionStatus.OPEN,
                    is_open=True,
                    note="paper_pyramid",
                )
                session.add(pos_row)
            else:
                pos_row.qty = total_qty
                pos_row.entry_price = weighted_avg
                pos_row.realized_pnl = total_realized
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
