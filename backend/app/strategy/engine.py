from __future__ import annotations

import asyncio
import time
from datetime import datetime, time as dt_time
from dataclasses import dataclass, asdict, fields as dc_fields
from typing import Dict, Optional, Protocol, List, Tuple
import uuid
from zoneinfo import ZoneInfo  # Python 3.9+

# ─────────────────────── SYMBOL BLACKLIST ───────────────────────
# Токсичные символы которые не торгуем
SYMBOL_BLACKLIST = {
    'ATOMUSDT',
    'TRXUSDT',
    'DOTUSDT',
    'LTCUSDT',  
    'SOLUSDT',
    'NEARUSDT',   # ✅ Added Jan 19, 2026: ~40 bps spread, toxic for HFT scalping
    # Add more toxic symbols here as discovered
}
# ────────────────────────────────────────────────────────────────

# ─────────────────────── COOLDOWN TRACKING ──────────────────────
# Tracks last trade timestamp per symbol (epoch seconds)
_last_trade_time: dict[str, float] = {}
# ────────────────────────────────────────────────────────────────

# ─────────────────────── TASK LIMITER ───────────────────────
# Limit concurrent database operations to prevent SQLite lock contention
_db_semaphore = asyncio.Semaphore(5)  # Max 5 concurrent DB operations
# ────────────────────────────────────────────────────────────

from app.models.trades import Trade
from app.db.session import SessionLocal
from zoneinfo import ZoneInfo



from app.config.constants import (
    MIN_SPREAD_BPS,
    EDGE_FLOOR_BPS,
    ABSORPTION_X_BPS,
    MAX_CONCURRENT_SYMBOLS,
    ORDER_SIZE_USD,
    TIMEOUT_EXIT_SEC,
    TAKE_PROFIT_BPS,           # NEW
    STOP_LOSS_BPS,             # NEW
    MIN_HOLD_MS,  
)
from app.services import book_tracker as bt_service
from app.services.mm_detector import get_mm_detector
from app.services.position_sizer import get_position_sizer, SizingMode
from app.execution.smart_executor import get_smart_executor
from app.config.settings import settings
from app.services.book_tracker import ensure_symbols_subscribed
from app.strategy.risk import get_risk_manager, calculate_dynamic_sl

# Metrics are optional; guard imports so the engine never crashes without them
try:
    from app.infra.metrics import (
        strategy_entries_total,
        strategy_exits_total,
        strategy_open_positions,
        strategy_realized_pnl_total,
        strategy_symbols_running,
        strategy_trade_pnl_bps,
        strategy_trade_duration_seconds,
        strategy_edge_bps_at_entry,
    )
    _METRICS_OK = True
except Exception:
    _METRICS_OK = False

# ═══════════════════════════════════════════════════════════════
# EXPLORATION INTEGRATION
# ═══════════════════════════════════════════════════════════════
try:
    from app.services.exploration import get_params_for_trade, get_exploration_stats
    _EXPLORATION_OK = True
    # ✅ ПРОВЕРЯЕМ РЕАЛЬНЫЙ СТАТУС:
    from app.services.exploration import exploration_manager
    if exploration_manager.config.enabled:
        print(f"[STRAT] ✅ Exploration enabled (rate={exploration_manager.config.exploration_rate:.0%})")
    else:
        print("[STRAT] ⚠️ Exploration disabled")
except Exception as e:
    _EXPLORATION_OK = False
    print(f"[STRAT] ⚠️ Exploration unavailable: {e}")
# ═══════════════════════════════════════════════════════════════


# ───────────────────────── Execution port contract ─────────────────────────
class ExecutionPort(Protocol):
    async def start_symbol(self, symbol: str) -> None: ...
    async def stop_symbol(self, symbol: str) -> None: ...
    async def flatten_symbol(self, symbol: str) -> None: ...
    async def cancel_orders(self, symbol: str) -> None: ...
    async def place_maker(self, symbol: str, side: str, price: float, qty: float, tag: str = "mm") -> Optional[str]: ...
    async def place_market(self, symbol: str, side: str, qty: float, tag: str = "mm") -> Optional[str]: ...
    async def get_position(self, symbol: str) -> dict: ...


# ───────────────────────────── Strategy params ─────────────────────────────
@dataclass
class StrategyParams:
    # Entry filters
    min_spread_bps: float = MIN_SPREAD_BPS
    edge_floor_bps: float = EDGE_FLOOR_BPS
    imbalance_min: float = 0.25
    imbalance_max: float = 0.75
    enable_depth_check: bool = False
    absorption_x_bps: float = ABSORPTION_X_BPS

    # Sizing & timing
    order_size_usd: float = ORDER_SIZE_USD
    timeout_exit_sec: int = TIMEOUT_EXIT_SEC
    max_concurrent_symbols: int = MAX_CONCURRENT_SYMBOLS

    # Trade management
    take_profit_bps: float = TAKE_PROFIT_BPS
    stop_loss_bps: float = STOP_LOSS_BPS
    min_hold_ms: int = MIN_HOLD_MS
    reenter_cooldown_ms: int = 1000
    min_seconds_between_trades: int = 30

    # Trailing Stop
    enable_trailing_stop: bool = False  # Will be loaded from settings
    trailing_activation_bps: float = 1.5
    trailing_stop_bps: float = 0.5
    trailing_step_bps: float = 0.3

    # Debug / testnet helper
    debug_force_entry: bool = False

    # Trading Schedule (Time Windows)
    trading_schedule_enabled: bool = False
    trading_start_time: str = "10:00"
    trading_end_time: str = "20:00"
    trading_timezone: str = "Europe/Istanbul"
    trade_on_weekends: bool = True
    close_before_end_minutes: int = 10


# ───────────────────────────── Per-symbol state ────────────────────────────
@dataclass
class SymbolState:
    running: bool = False
    task: Optional[asyncio.Task] = None
    last_entry_ts: int = 0
    last_exit_ts: int = 0
    last_error: str = ""
    cooldown_reset_at_ms: int = 0
    # Trade logging
    current_trade_id: Optional[str] = None
    current_trade_db_id: Optional[int] = None
    entry_dynamic_sl: float = -3.0  # Dynamic stop loss calculated at entry
    
    # Trailing Stop tracking (NEW)
    trailing_active: bool = False
    trailing_stop_price: float = 0.0
    peak_price: float = 0.0

    # ✅ ADD: Store exploration params for current trade
    trade_take_profit_bps: float = 2.0
    trade_stop_loss_bps: float = -2.0
    trade_trailing_enabled: bool = True
    trade_trail_activation: float = 1.8
    trade_trail_distance: float = 0.5
    trade_timeout_sec: float = 40.0
    trade_is_exploration: bool = False
    
    # ═══ HARD PROTECTION (NEW - Jan 19, 2026) ═══
    hard_sl_triggered: bool = False


# ───────────────────────────── Strategy engine ─────────────────────────────
class StrategyEngine:
    def __init__(self, exec_port: ExecutionPort, params: Optional[StrategyParams] = None) -> None:
        self._exec = exec_port
        self._params = params or StrategyParams()
        
        # ✅ Load trailing stop settings from config
        self._params.enable_trailing_stop = settings.trailing_stop_enabled
        self._params.trailing_activation_bps = settings.trailing_activation_bps
        self._params.trailing_stop_bps = settings.trailing_distance_bps
        
        self._symbols: Dict[str, SymbolState] = {}
        self._lock = asyncio.Lock()

    # ───────── public operations ─────────
    async def start_symbols(self, symbols: List[str]) -> None:
        """Start (or restart) trading for the given symbols (respecting max_concurrent_symbols)."""
        syms = [s.upper() for s in symbols if s and s.strip()]
        if not syms:
            return
        
        # Filter out blacklisted symbols
        syms = [s for s in syms if s not in SYMBOL_BLACKLIST]
        if not syms:
            print("[STRAT] All symbols blacklisted, nothing to start")
            return

        # Ensure quotes are flowing (works across providers)
        try:
            await ensure_symbols_subscribed(syms)
        except Exception:
            pass

        async with self._lock:
            active = [s for s, st in self._symbols.items() if st.running]
            can_start = max(0, int(self._params.max_concurrent_symbols) - len(active))
            to_start = syms[:can_start]

            for sym in to_start:
                st = self._symbols.setdefault(sym, SymbolState())

                # Treat start as RESTART if already running
                if st.task and not st.task.done():
                    st.task.cancel()
                    try:
                        await asyncio.wait_for(st.task, timeout=1.5)
                    except Exception:
                        pass

                st.running = True
                st.last_error = ""
                st.cooldown_reset_at_ms = int(time.time() * 1000)  # clear local cooldown
                st.task = asyncio.create_task(self._symbol_loop(sym))

                # Let the execution port warm any per-symbol state
                try:
                    await self._exec.start_symbol(sym)
                except Exception:
                    pass

                if _METRICS_OK:
                    try:
                        strategy_open_positions.labels(sym).set(0)
                        pos = await self._exec.get_position(sym)
                        strategy_realized_pnl_total.labels(sym).set(float(pos.get("realized_pnl", 0.0)))
                    except Exception:
                        pass

                print(f"[STRAT] ▶ start {sym}")

            skipped = set(syms) - set(to_start)
            if skipped:
                print(
                    f"[STRAT] ⚠ max_concurrent_symbols={self._params.max_concurrent_symbols}; "
                    f"skipped: {sorted(skipped)}"
                )

    async def stop_symbols(self, symbols: List[str], flatten: bool = False) -> None:
        syms = [s.upper() for s in symbols if s and s.strip()]
        if not syms:
            return

        async with self._lock:
            for sym in syms:
                st = self._symbols.get(sym)
                if not st:
                    continue
                st.running = False

                # CHANGED: сначала отменяем лимитки, затем, при необходимости, флаттим
                try:
                    await self._exec.cancel_orders(sym)  # CHANGED
                except Exception:
                    pass

                if flatten:
                    try:
                        await self._exec.flatten_symbol(sym)
                    except Exception:
                        pass

                try:
                    await self._exec.stop_symbol(sym)
                except Exception:
                    pass

                if _METRICS_OK:
                    try:
                        strategy_open_positions.labels(sym).set(0)
                        pos = await self._exec.get_position(sym)
                        strategy_realized_pnl_total.labels(sym).set(float(pos.get("realized_pnl", 0.0)))
                    except Exception:
                        pass

                # Cancel and await the symbol loop briefly
                if st.task and not st.task.done():
                    st.task.cancel()
                    try:
                        await asyncio.wait_for(st.task, timeout=1.5)
                    except Exception:
                        pass

                # CHANGED: если таск уже завершён — подчистим словарь
                if not (st.task and not st.task.done()):
                    self._symbols.pop(sym, None)

                print(f"[STRAT] ⏹ stop {sym}")

    async def stop_all(self, flatten: bool = False) -> None:
        """Stop all active symbols."""
        await self.stop_symbols(list(self._symbols.keys()), flatten=flatten)

    # ───────── internal helpers ─────────
    async def _wait_warm_quotes(self, symbol: str, min_events: int = 3, timeout_ms: int = 2000) -> bool:
        """Wait until we see several non-zero (bid, ask, mid) quotes so first decisions are valid."""
        deadline = time.time() + (timeout_ms / 1000.0)
        ok = 0
        while time.time() < deadline:
            q = await bt_service.get_quote(symbol)
            bid = float(q.get("bid", 0.0))
            ask = float(q.get("ask", 0.0))
            mid = float(q.get("mid", 0.0))
            if bid > 0.0 and ask > 0.0 and mid > 0.0:
                ok += 1
                if ok >= min_events:
                    return True
            await asyncio.sleep(0.08)
        return False
    
    def _is_trading_allowed(self) -> tuple[bool, str]:
        """
        Check if trading is allowed based on schedule settings.
        Returns: (allowed: bool, reason: str)
        """
        p = self._params
        
        if not p.trading_schedule_enabled:
            return True, "schedule_disabled"
        
        try:
            # Get current time in configured timezone
            tz = ZoneInfo(p.trading_timezone)
            now = datetime.now(tz)
            
            # Check weekends
            if not p.trade_on_weekends:
                if now.weekday() >= 5:  # Saturday=5, Sunday=6
                    return False, "weekend"
            
            # Parse time strings (HH:MM format)
            start_h, start_m = map(int, p.trading_start_time.split(":"))
            end_h, end_m = map(int, p.trading_end_time.split(":"))
            
            start_time = dt_time(start_h, start_m)
            end_time = dt_time(end_h, end_m)
            current_time = now.time()
            
            # Check if current time is within window
            if start_time <= end_time:
                # Normal case: 10:00 - 20:00
                in_window = start_time <= current_time <= end_time
            else:
                # Overnight case: 22:00 - 02:00
                in_window = current_time >= start_time or current_time <= end_time
            
            if not in_window:
                return False, f"outside_window ({p.trading_start_time}-{p.trading_end_time})"
            
            return True, "ok"
            
        except Exception as e:
            # If timezone parsing fails, log and allow trading (fail open)
            print(f"[SCHEDULE] ⚠️ Error checking schedule: {e}")
            return True, "check_error"
    
    def _should_close_before_end(self) -> tuple[bool, str]:
        """
        Check if we should close positions before end of trading window.
        Returns: (should_close: bool, reason: str)
        """
        p = self._params
        
        if not p.trading_schedule_enabled:
            return False, "schedule_disabled"
        
        try:
            tz = ZoneInfo(p.trading_timezone)
            now = datetime.now(tz)
            
            # Parse end time
            end_h, end_m = map(int, p.trading_end_time.split(":"))
            end_time = dt_time(end_h, end_m)
            
            # Calculate time until end
            end_datetime = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
            if end_datetime < now:
                # End time is tomorrow
                from datetime import timedelta
                end_datetime += timedelta(days=1)
            
            minutes_until_end = (end_datetime - now).total_seconds() / 60
            
            if minutes_until_end <= p.close_before_end_minutes:
                return True, f"close_window ({minutes_until_end:.1f}min_remaining)"
            
            return False, "ok"
            
        except Exception as e:
            print(f"[SCHEDULE] ⚠️ Error checking close window: {e}")
            return False, "check_error"

    # ───────── per-symbol loop ─────────
    async def _symbol_loop(self, symbol: str) -> None:
        sym = symbol.upper()
        print(f"[STRAT:{sym}] loop started")

        if _METRICS_OK:
            try:
                strategy_symbols_running.inc()
            except Exception:
                pass

        poll_ms = 50  # ✅ FIX: Faster reaction (was 120ms, price can move 1%+ in 120ms)

        # ═══════════════════════════════════════════════════════════
        # PYRAMID: Track list of positions (NEW)
        # ═══════════════════════════════════════════════════════════
        positions_list: List[Dict] = []

        # Seed position from executor (survives restarts)
        in_pos = False
        entry_px = 0.0
        entry_ts = 0.0
        qty_units = 0.0

        # Warm-up quotes before first decision
        await self._wait_warm_quotes(sym)

        # If we already hold a long, reflect it in local state
        try:
            pos = await self._exec.get_position(sym)
            qty_f = float(pos.get("qty", 0.0) or 0.0)
            avg_px = float(pos.get("avg_price", 0.0) or 0.0)
            
            if qty_f > 0.0:
                in_pos = True
                qty_units = qty_f
                entry_px = avg_px
                entry_ts = time.time()
                
                # Add to pyramid tracking
                # ts_ms из DB в миллисекундах, конвертируем в секунды
                real_entry_ts = pos.get("ts_ms", 0) / 1000.0 if pos.get("ts_ms", 0) > 0 else time.time()
                positions_list.append({
                    'qty': qty_f,
                    'entry_price': avg_px,
                    'entry_ts': real_entry_ts,
                })
                
                if _METRICS_OK:
                    try:
                        strategy_open_positions.labels(sym).set(1)
                    except Exception:
                        pass
        except Exception:
            pass

        last_exit_ts_ms = 0.0
        st = self._symbols.get(sym)
        if st and st.cooldown_reset_at_ms:
            last_exit_ts_ms = st.cooldown_reset_at_ms

        try:
            while True:
                st = self._symbols.get(sym)
                if not (st and st.running):
                    break

                # 🚫 BLACKLIST CHECK - safety net in case symbol was started before blacklist
                if sym in SYMBOL_BLACKLIST:
                    print(f"[STRAT:{sym}] 🚫 BLACKLISTED - stopping immediately")
                    st.running = False
                    return

                # always read latest params so PUT /params hot-applies
                # always read latest params so PUT /params hot-applies
                # always read latest params so PUT /params hot-applies
                p = self._params
                
                # 🔥 Get fresh market data from scanner (HTTP is fastest & most reliable)
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        r = await client.get(
                            "http://localhost:8000/api/scanner/mexc/top",
                            params={"symbols": sym, "limit": 1}
                        )
                        
                        if r.status_code == 200:
                            data = r.json()
                            if data and len(data) > 0 and data[0].get("bid", 0) > 0:
                                row = data[0]
                                bid = float(row["bid"])
                                ask = float(row["ask"])
                                mid = (bid + ask) / 2
                                spread_bps = float(row["spread_bps"])
                                imb = float(row["imbalance"])
                                abs_bid_usd = float(row.get("depth5_bid_usd", 0))
                                abs_ask_usd = float(row.get("depth5_ask_usd", 0))
                                # ═══ Feed to MM Detector ═══
                                try:
                                    mm_detector = get_mm_detector()
                                    await mm_detector.on_book_update(
                                        symbol=sym,
                                        best_bid=bid,
                                        best_ask=ask,
                                        bid_size=abs_bid_usd / bid if bid > 0 else 0.0,
                                        ask_size=abs_ask_usd / ask if ask > 0 else 0.0
                                    )
                                except Exception:
                                    pass  # Silent fail - MM detection is optional
                                # ═══════════════════════════
                            else:
                                raise Exception("Empty scanner response")
                        else:
                            raise Exception(f"Scanner returned {r.status_code}")
                
                except Exception as e:
                    # Fallback to cache
                    q = await bt_service.get_quote(sym)
                    bid = float(q.get("bid", 0))
                    ask = float(q.get("ask", 0))
                    mid = float(q.get("mid", 0))
                    spread_bps = float(q.get("spread_bps", 0))
                    imb = 0.5
                    abs_bid_usd = 0.0
                    abs_ask_usd = 0.0
                
                
                if bid <= 0.0 or ask <= 0.0 or mid <= 0.0:
                    await asyncio.sleep(poll_ms / 1000)
                    continue

                now = time.time()

                # Apply cooldown only between entries (not after exits)
                if not in_pos:
                    # re-enter cooldown
                    if (now * 1000 - last_exit_ts_ms) < p.reenter_cooldown_ms:
                        await asyncio.sleep(poll_ms / 1000)
                        continue

                    # ⏰ SCHEDULE CHECK - block entry outside trading window
                    allowed, reason = self._is_trading_allowed()
                    if not allowed:
                        if not hasattr(st, '_last_schedule_log') or (now - st._last_schedule_log) > 30:
                            st._last_schedule_log = now
                            print(f"[STRAT:{sym}] ⏰ Trading not allowed: {reason}")
                        await asyncio.sleep(poll_ms / 1000)
                        continue


                    # 🔍 DEBUG: Print what we see every 10 seconds
                    if not hasattr(st, '_last_debug') or (now - st._last_debug) > 10:
                        st._last_debug = now
                        print(f"[STRAT:{sym}] 🔍 bid={bid:.6f} ask={ask:.6f} mid={mid:.6f} "
                              f"spread_bps={spread_bps:.2f} imb={imb:.3f} "
                              f"min_spread={p.min_spread_bps} edge_floor={p.edge_floor_bps}")

                    # debug bypass for demo/testnets
                    if p.debug_force_entry:
                        qty_units = max(0.0, p.order_size_usd / bid)
                        if qty_units > 0.0:
                            oid = await self._exec.place_maker(sym, "BUY", price=bid, qty=qty_units, tag="mm_entry_dbg")
                            if oid:
                                in_pos = True
                                entry_px = bid
                                entry_ts = now
                                st.last_entry_ts = int(entry_ts * 1000)
                                
                                # ✅ ИСПОЛЬЗУЕМ DEFAULT ПАРАМЕТРЫ
                                st.trade_take_profit_bps = p.take_profit_bps
                                st.trade_stop_loss_bps = p.stop_loss_bps
                                st.trade_trailing_enabled = p.enable_trailing_stop
                                st.trade_trail_activation = p.trailing_activation_bps
                                st.trade_trail_distance = p.trailing_stop_bps
                                st.trade_timeout_sec = float(p.timeout_exit_sec)
                                st.trade_is_exploration = False
                                if _METRICS_OK:
                                    try:
                                        strategy_entries_total.labels(sym).inc()
                                        strategy_open_positions.labels(sym).set(1)
                                        strategy_edge_bps_at_entry.labels(sym).observe(max(0.0, spread_bps))
                                    except Exception:
                                        pass
                                print(f"[STRAT:{sym}] DEBUG ENTRY BUY qty={qty_units:.6f} @ {bid}")
                        await asyncio.sleep(poll_ms / 1000)
                        continue

                    # entry filters (spot, long-only)
                    base_ok = (spread_bps >= p.min_spread_bps) and (p.imbalance_min <= imb <= p.imbalance_max)
                    depth_ok = True
                    if p.enable_depth_check:
                        # We BUY now → later we will SELL, so require that ask side can fill entry size
                        depth_ok = (abs_ask_usd >= p.order_size_usd)
                    edge_ok = (spread_bps >= p.edge_floor_bps)
                    
                    # ═══ CRITICAL CHECK (Jan 19, 2026): Reject TOXIC spreads! ═══
                    # If spread > 20 bps, we cannot profit with 2-3 bps TP target
                    # Example: NEARUSDT often has 30-50 bps spread = instant -27 to -47 bps loss!
                    MAX_SPREAD_BPS = 20.0  # Maximum acceptable spread for scalping
                    spread_ok = spread_bps <= MAX_SPREAD_BPS
                    if not spread_ok and not hasattr(st, '_last_spread_warn') or (now - getattr(st, '_last_spread_warn', 0)) > 60:
                        st._last_spread_warn = now
                        print(f"[STRAT:{sym}] ⚠️ TOXIC SPREAD: {spread_bps:.1f} bps > {MAX_SPREAD_BPS} - SKIPPING ENTRY")
                    # ═══════════════════════════════════════════════════════════

                    # ═══════════════════════════════════════════════════════
                    # RISK CHECKS BEFORE ENTRY
                    # ═══════════════════════════════════════════════════════
                    risk_ok = True
                    try:
                        risk_manager = get_risk_manager()
                        
                        # Check if trading is allowed
                        if not risk_manager.can_trade():
                            risk_ok = False
                            # Check every 30 seconds if we should log halt reason
                            if not hasattr(st, '_last_halt_log') or (now - st._last_halt_log) > 30:
                                st._last_halt_log = now
                                print(f"[STRAT:{sym}] ⚠️ Trading halted: {risk_manager.state.halt_reason or 'unknown'}")
                        
                        # Check symbol-specific cooldown
                        elif risk_manager.is_symbol_on_cooldown(sym):
                            risk_ok = False
                            remaining = risk_manager.state.get_cooldown_remaining_seconds(sym)
                            if not hasattr(st, '_last_cooldown_log') or (now - st._last_cooldown_log) > 30:
                                st._last_cooldown_log = now
                                # print(f"[STRAT:{sym}] ⏸️ Cooldown active: {remaining:.0f}s remaining")
                        
                        # Check if we can open new position
                        elif not await risk_manager.can_open_position(sym, p.order_size_usd):
                            risk_ok = False
                            if not hasattr(st, '_last_limit_log') or (now - st._last_limit_log) > 30:
                                st._last_limit_log = now
                                print(f"[STRAT:{sym}] 🚫 Position limit reached or exposure too high")
                    
                    except Exception as e:
                        print(f"[STRAT:{sym}] ⚠️ Risk check failed: {e}")
                        risk_ok = False
                    # ═══════════════════════════════════════════════════════

                    if base_ok and depth_ok and edge_ok and risk_ok and spread_ok:
                        # ═══════════════════════════════════════════════════════
                        # MM DETECTION CHECK (Phase 2)
                        # ═══════════════════════════════════════════════════════
                        mm_ok = True
                        mm_safe_size = p.order_size_usd  # Default
                        
                        try:
                            mm_detector = get_mm_detector()
                            mm_pattern = mm_detector.get_pattern(sym)
                            
                            if mm_pattern:
                                # MM detected - use safe size
                                mm_safe_size = mm_pattern.safe_order_size_usd
                                
                                # Log once per 30s
                                if not hasattr(st, '_last_mm_log') or (now - st._last_mm_log) > 30:
                                    st._last_mm_log = now
                                    print(
                                        f"[MM] ✅ {sym} conf={mm_pattern.mm_confidence:.2%} "
                                        f"safe=${mm_safe_size:.2f}"
                                    )
                            else:
                                # No MM - use default
                                if not hasattr(st, '_last_no_mm_log') or (now - st._last_no_mm_log) > 60:
                                    st._last_no_mm_log = now
                                    print(f"[MM] ⚠️ {sym} not detected (default size)")
                        
                        except Exception as e:
                            print(f"[MM] ⚠️ {sym} error: {e}")
                            mm_ok = True  # Fail open
                        
                        if not mm_ok:
                            await asyncio.sleep(0.1)
                            continue
                        # ═══════════════════════════════════════════════════════
                        # ═══════════════════════════════════════════════════════
                        # ML FILTER CHECK
                        # ═══════════════════════════════════════════════════════
                        ml_ok = True
                        try:
                            from app.config.settings import settings
                            if settings.ML_ENABLED:
                                from app.services.ml_predictor import get_ml_predictor
                                ml_pred = get_ml_predictor()
                                
                                features = {
                                    "symbol": sym,
                                    "spread_bps_entry": spread_bps,
                                    "imbalance_entry": imb,
                                }
                                
                                # Get ML prediction
                                ml_score = await ml_pred.predict(features)
                                should_enter = ml_score >= settings.ML_MIN_CONFIDENCE
                                
                                if not should_enter:
                                    ml_ok = False
                                    if not hasattr(st, '_last_ml_log') or (now - st._last_ml_log) > 30:
                                        st._last_ml_log = now
                                        print(f"[ML] ❌ Filtered: {sym} ml_score={ml_score:.3f} < {settings.ML_MIN_CONFIDENCE}")
                                else:
                                    # Log pass only once per 30 seconds to avoid spam
                                    if not hasattr(st, '_last_ml_pass_log') or (now - st._last_ml_pass_log) > 30:
                                        st._last_ml_pass_log = now
                                        print(f"[ML] ✅ Passed: {sym} ml_score={ml_score:.3f} >= {settings.ML_MIN_CONFIDENCE}")
                        
                        except asyncio.TimeoutError:
                            # ML prediction timed out - fail open
                            print(f"[ML] ⏱️ Timeout for {sym}, allowing entry")
                            ml_ok = True
                        
                        except Exception as e:
                            print(f"[ML] ⚠️ Error filtering {sym}: {e}")
                            ml_ok = True  # Fail open - allow trade if ML errors
                        
                        if not ml_ok:
                            await asyncio.sleep(0.1)
                            continue

                        # ═══════════════════════════════════════════════════════════
                        # POSITION SIZER: Calculate optimal size (Phase 2)
                        # ═══════════════════════════════════════════════════════════
                        try:
                            position_sizer = get_position_sizer()
                            
                            # Calculate position size using MM detector output
                            position_size = position_sizer.calculate_size(
                                symbol=sym,
                                target_size_usd=mm_safe_size,  # ✅ NOW AVAILABLE!
                                mode=SizingMode.CONSERVATIVE
                            )
                            
                            # Store for later use
                            final_size_usd = position_size.safe_size_usd
                            max_positions = position_size.split_count
                            
                            # Log sizing decision (once per 30s)
                            if not hasattr(st, '_last_size_log') or (now - st._last_size_log) > 30:
                                st._last_size_log = now
                                print(
                                    f"[SIZE] {sym} target=${mm_safe_size:.2f} "
                                    f"final=${final_size_usd:.2f} "
                                    f"max_positions={max_positions} "
                                    f"risk={position_size.risk_level}"
                                )

                        except Exception as e:
                            # Fallback to mm_safe_size if position sizer fails
                            print(f"[SIZE] ⚠️ {sym} error: {e}")
                            final_size_usd = mm_safe_size
                            max_positions = 1
                        # ═══════════════════════════════════════════════════════════
                        
                        # ═══════════════════════════════════════════════════════
                        # EXPLORATION: Get parameters (random or default)
                        # ═══════════════════════════════════════════════════════
                        actual_tp = p.take_profit_bps
                        actual_sl = p.stop_loss_bps
                        actual_trailing = p.enable_trailing_stop
                        actual_trail_activation = p.trailing_activation_bps
                        actual_trail_distance = p.trailing_stop_bps
                        actual_timeout = float(p.timeout_exit_sec)
                        is_exploration = False

                        if _EXPLORATION_OK:
                            try:
                                # Default parameters from config
                                default_params = {
                                    'take_profit_bps': p.take_profit_bps,
                                    'stop_loss_bps': p.stop_loss_bps,
                                    'trailing_stop_enabled': p.enable_trailing_stop,
                                    'trail_activation_bps': p.trailing_activation_bps,
                                    'trail_distance_bps': p.trailing_stop_bps,
                                    'timeout_seconds': float(p.timeout_exit_sec)
                                }
                                
                                # Get params (exploration or exploitation)
                                trade_params, is_exploration = get_params_for_trade(sym, default_params)
                                
                                # Override with exploration params
                                actual_tp = trade_params['take_profit_bps']
                                actual_sl = trade_params['stop_loss_bps']
                                actual_trailing = trade_params['trailing_stop_enabled']
                                actual_trail_activation = trade_params['trail_activation_bps']
                                actual_trail_distance = trade_params['trail_distance_bps']
                                actual_timeout = trade_params['timeout_seconds']
                                
                                if is_exploration:
                                    print(
                                        f"[EXPLORATION] {sym}: TP={actual_tp:.1f}, SL={actual_sl:.1f}, "
                                        f"Trail={'ON' if actual_trailing else 'OFF'}, Timeout={actual_timeout:.0f}s"
                                    )
                            
                            except Exception as e:
                                print(f"[EXPLORATION] ⚠️ Failed: {e}, using default params")
                                # Fallback already set above
                        # ═══════════════════════════════════════════════════════
                        else:
                            # No ML Collector - use default
                            actual_tp = p.take_profit_bps
                            actual_sl = p.stop_loss_bps
                            actual_trailing = p.enable_trailing_stop
                            actual_trail_activation = p.trailing_activation_bps
                            actual_trail_distance = p.trailing_stop_bps
                            actual_timeout = float(p.timeout_exit_sec)
                            is_exploration = False
                        # ═══════════════════════════════════════════════════════
                        
                        # COOLDOWN CHECK
                        now_ts = time.time()
                        last_trade = _last_trade_time.get(sym, 0)
                        cooldown_seconds = p.min_seconds_between_trades

                        if (now_ts - last_trade) < cooldown_seconds:
                            remaining = cooldown_seconds - (now_ts - last_trade)
                            await asyncio.sleep(0.1)
                            continue

                        # ═══════════════════════════════════════════════════════════
                        # CALCULATE POSITION SIZE
                        # ═══════════════════════════════════════════════════════════
                        qty_units = max(0.0, final_size_usd / bid) if bid > 0 else 0.0

                        if qty_units > 0.0:
                            # ═══════════════════════════════════════════════════════
                            # SMART EXECUTOR: MM-aware entry with splitting (Phase 2)
                            # ═══════════════════════════════════════════════════════
                            try:
                                smart_executor = get_smart_executor()
                                
                                # Execute entry (with splitting if needed)
                                fill_result = await smart_executor.execute_entry(
                                    executor=self._exec,
                                    symbol=sym,
                                    side="BUY",
                                    price=bid,
                                    total_qty=qty_units,
                                    split_count=position_size.split_count,
                                    split_delay_sec=position_size.split_delay_sec
                                )
                                
                                # Check if filled
                                oid = fill_result.get('order_id') if fill_result else None
                                filled_qty = fill_result.get('filled_qty', 0.0) if fill_result else 0.0
                                
                                # Log execution quality
                                if fill_result and not hasattr(st, '_last_exec_log') or (now - st._last_exec_log) > 30:
                                    st._last_exec_log = now
                                    print(
                                        f"[EXEC] {sym} quality={fill_result.get('quality', 0):.1%} "
                                        f"slippage={fill_result.get('slippage_bps', 0):.2f}bps "
                                        f"splits={fill_result.get('actual_splits', 1)}"
                                    )
                            
                            except Exception as e:
                                # Fallback to simple order
                                print(f"[EXEC] ⚠️ SmartExecutor failed: {e}, using simple order")
                                oid = await self._exec.place_maker(sym, "BUY", price=bid, qty=qty_units, tag="mm_entry")
                                filled_qty = qty_units
                            
                            # ═══════════════════════════════════════════════════════
                               
                            if oid:
                                # Update last trade time AFTER successful order
                                _last_trade_time[sym] = now_ts
                                
                                # ═══ PYRAMID: Add position to tracking list ═══
                                positions_list.append({
                                    'qty': filled_qty or qty_units,
                                    'entry_price': bid,
                                    'entry_ts': now,
                                })
                                
                                st.last_entry_ts = int(now * 1000)

                                # ✅ КРИТИЧНО: Обновляем состояние позиции!
                                in_pos = True
                                entry_px = bid
                                entry_ts = now
                                qty_units = filled_qty or qty_units
                                
                                print(f"[PYRAMID] {sym}: Added position #{len(positions_list)}, "
                                    f"total positions={len(positions_list)}, "
                                    f"total_qty={sum(p['qty'] for p in positions_list):.6f}")
                                
                                # ✅ SAVE PARAMS FOR THIS TRADE
                                st.trade_take_profit_bps = actual_tp
                                st.trade_stop_loss_bps = actual_sl
                                st.trade_trailing_enabled = actual_trailing
                                st.trade_trail_activation = actual_trail_activation
                                st.trade_trail_distance = actual_trail_distance
                                st.trade_timeout_sec = actual_timeout
                                st.trade_is_exploration = is_exploration
                                
                                # ═══ CALCULATE DYNAMIC STOP LOSS (AFTER ENTRY) ═══
                                atr_pct = 0.10  # TODO: Get from candles_cache when available
                                dynamic_sl = calculate_dynamic_sl(
                                    atr_pct=atr_pct,
                                    spread_bps=spread_bps,
                                    imbalance=imb,
                                    base_sl_bps=p.stop_loss_bps
                                )
                                st.entry_dynamic_sl = dynamic_sl
                                print(
                                    f"[STRAT:{sym}] 📊 Dynamic SL: {dynamic_sl:.2f} bps "
                                    f"(ATR:{atr_pct:.2%}, Spread:{spread_bps:.1f}, Imb:{imb:.2f})"
                                )
                                # ═══════════════════════════════════════════════════
                                
                                # ═══ LOGGING: Create trade entry ═══
                                
                                # ═══ LOGGING: Create trade entry ═══
                                # ═══ LOGGING: Create trade entry (NON-BLOCKING) ═══
                                trade_id = f"{sym}_{uuid.uuid4().hex[:8]}"
                                st.current_trade_id = trade_id

                                async def _log_entry():
                                    async with _db_semaphore:  # ← ADD THIS LINE
                                        db = None  # ← ADD THIS LINE
                                        try:
                                            db = SessionLocal()
                                            trade = Trade.create_entry(
                                                trade_id=trade_id,
                                                symbol=sym,
                                                entry_time=datetime.fromtimestamp(entry_ts),
                                                entry_price=bid,
                                                entry_qty=qty_units,
                                                entry_side="BUY",
                                                entry_fee=0.0,
                                                spread_bps=spread_bps,
                                                imbalance=imb,
                                                depth_5bps=abs_bid_usd + abs_ask_usd,
                                                strategy_tag="mm_entry",
                                                exchange="MEXC"
                                            )
                                            db.add(trade)
                                            db.commit()
                                            st.current_trade_db_id = trade.id
                                        except Exception as e:
                                            print(f"[STRAT:{sym}] ⚠️ Failed to log entry: {e}")
                                        finally:  # ← CHANGE except to finally
                                            if db:  # ← ADD THIS CHECK
                                                try:
                                                    db.close()
                                                except:
                                                    pass

                                # Run in background (don't wait)
                                asyncio.create_task(_log_entry())
                                # ═══════════════════════════════════
                                # ═══════════════════════════════════

                                # ═══ ML TRADE LOGGER: Log entry with full features ═══
                                try:
                                    from app.services.ml_trade_logger import get_ml_trade_logger
                                    ml_logger = get_ml_trade_logger()
                                    
                                    # Step 1: Get FULL scanner data with all available features
                                    scan_data = None
                                    try:
                                        import httpx
                                        async with httpx.AsyncClient(timeout=2.0) as client:
                                            r = await client.get(
                                                "http://localhost:8000/api/scanner/mexc/top",
                                                params={"symbols": sym, "limit": 1}
                                            )
                                            if r.status_code == 200:
                                                data = r.json()
                                                if data and len(data) > 0:
                                                    scan_data = data[0]  # Full scanner row with ALL features
                                                    print(f"[ML_LOGGER] 📊 Got scanner data: "
                                                          f"trades/min={scan_data.get('trades_per_min', 0):.1f}, "
                                                          f"usd/min={scan_data.get('usd_per_min', 0):.1f}")
                                    except Exception as e:
                                        print(f"[ML_LOGGER] ⚠️ Failed to get scanner data: {e}")
                                    
                                    # Step 2: Enrich with ALL candle features
                                    if scan_data:
                                        try:
                                            from app.services.candles_cache import candles_cache
                                            
                                            # Get candle stats (cached, fast)
                                            candle_stats = await candles_cache.get_stats(sym, venue="mexc", refresh=False)
                                            
                                            # Merge ALL candle features into scan_data
                                            if candle_stats:
                                                scan_data['atr1m_pct'] = candle_stats.get('atr1m_pct', 0.0)
                                                scan_data['spike_count_90m'] = candle_stats.get('spike_count_90m', 0)
                                                scan_data['grinder_ratio'] = candle_stats.get('grinder_ratio', 0.0)
                                                scan_data['pullback_median_retrace'] = candle_stats.get('pullback_median_retrace', 0.35)
                                                scan_data['range_stable_pct'] = candle_stats.get('range_stable_pct', 0.0)
                                                scan_data['vol_pattern'] = candle_stats.get('vol_pattern', 0)
                                                scan_data['dca_potential'] = candle_stats.get('dca_potential', 0)
                                                
                                                print(f"[ML_LOGGER] 📈 Got candle data: "
                                                      f"atr={candle_stats.get('atr1m_pct', 0):.4f}, "
                                                      f"grinder={candle_stats.get('grinder_ratio', 0):.2f}, "
                                                      f"spikes={candle_stats.get('spike_count_90m', 0)}")
                                        except Exception as e:
                                            print(f"[ML_LOGGER] ⚠️ Failed to get candle data: {e}")
                                    
                                    # Step 3: Fallback to basic data if scanner failed completely
                                    if not scan_data:
                                        print(f"[ML_LOGGER] ⚠️ Using fallback data (scanner unavailable)")
                                        scan_data = {
                                            'spread_bps': spread_bps,
                                            'imbalance': imb,
                                            'depth_at_bps': {
                                                5: {
                                                    'bid_usd': abs_bid_usd,
                                                    'ask_usd': abs_ask_usd
                                                }
                                            },
                                            'eff_spread_maker_bps': spread_bps,
                                            'trades_per_min': 0.0,
                                            'usd_per_min': 0.0,
                                            'median_trade_usd': 0.0,
                                            'atr1m_pct': 0.0,
                                            'grinder_ratio': 0.0,
                                            'pullback_median_retrace': 0.35,
                                        }
                                    
                                    strategy_params = {
                                        'take_profit_bps': actual_tp,
                                        'stop_loss_bps': actual_sl,
                                        'trailing_stop_enabled': actual_trailing,
                                        'trail_activation_bps': actual_trail_activation,
                                        'trail_distance_bps': actual_trail_distance,
                                        'timeout_seconds': actual_timeout,
                                        'exploration_mode': 1 if is_exploration else 0,
                                    }
                                    
                                    ml_logger.log_entry(
                                        symbol=sym,
                                        scan_row=scan_data,
                                        strategy_params=strategy_params,
                                        entry_price=bid,
                                        entry_qty=qty_units,
                                        trade_id=trade_id,
                                    )
                                    
                                    print(f"[ML_LOGGER] ✅ Entry logged: {trade_id}")
                                    
                                except Exception as e:
                                    print(f"[ML_LOGGER] ⚠️ Failed to log entry: {e}")
                                    import traceback
                                    traceback.print_exc()
                                # ═════════════════════════════════════════════════════
                                
                                if _METRICS_OK:
                                    try:
                                        strategy_entries_total.labels(sym).inc()
                                        strategy_open_positions.labels(sym).set(1)
                                        strategy_edge_bps_at_entry.labels(sym).observe(max(0.0, spread_bps))
                                    except Exception:
                                        pass
                                print(f"[STRAT:{sym}] ENTRY BUY qty={qty_units:.6f} @ {bid}")

                else:
                    # ═══ PYRAMID: Calculate PnL for ALL positions ═══
                    if not positions_list:
                        # No positions, skip exit logic
                        await asyncio.sleep(poll_ms / 1000)
                        continue
                    
                    # Use oldest position for timing
                    oldest_pos = positions_list[0]
                    elapsed_s = now - oldest_pos['entry_ts']

                    # ⚠️ MM GONE CHECK - Emergency exit
                    mm_detector = get_mm_detector()
                    mm_gone, mm_reason = mm_detector.is_mm_gone(sym, spread_bps)
                    if mm_gone:
                        print(f"[STRAT:{sym}] 🚨 MM GONE: {mm_reason} - EMERGENCY EXIT")
                        pos = await self._exec.get_position(sym)
                        actual_qty = float(pos.get("qty", 0.0))
                        if actual_qty > 0:
                            await self._exec.place_market(sym, "SELL", qty=actual_qty, tag="mm_exit_emergency")
                            await self._exec.flatten_symbol(sym)
                            in_pos = False
                            positions_list.clear()
                            last_exit_ts_ms = time.time() * 1000
                            if _METRICS_OK:
                                strategy_exits_total.labels(sym, "MM_GONE").inc()
                                strategy_open_positions.labels(sym).set(0)
                        await asyncio.sleep(poll_ms / 1000)
                        continue
                    
                    # Calculate weighted average PnL
                    total_qty = sum(p['qty'] for p in positions_list)
                    total_cost = sum(p['qty'] * p['entry_price'] for p in positions_list)
                    avg_entry = total_cost / total_qty if total_qty > 0 else 0.0
                    
                    # ═══ CRITICAL FIX (Jan 19, 2026): Use BID for PnL, not MID! ═══
                    # For BUY positions, we exit by SELLing at BID price
                    # MID is misleading when spread is wide (e.g., NEARUSDT 40 bps spread)
                    # Example: entry=1.556, mid=1.553, bid=1.55 → mid shows -2 bps, bid shows -39 bps!
                    pnl_bps = (bid - avg_entry) / avg_entry * 1e4 if avg_entry > 0 else 0.0
                    pnl_bps_mid = (mid - avg_entry) / avg_entry * 1e4 if avg_entry > 0 else 0.0  # For logging only

                    # ═══════════════════════════════════════════════════════════
                    # 🚨 LAYER 1: HARD STOP LOSS - CHECK FIRST, ALWAYS!
                    # This is insurance against catastrophic losses.
                    # If triggered, we exit IMMEDIATELY with MARKET order.
                    # NO LIMIT attempts, NO delays.
                    # ═══════════════════════════════════════════════════════════
                    HARD_SL_BPS = -10.0  # Absolute maximum loss per trade
                    
                    if pnl_bps <= HARD_SL_BPS:
                        print(f"[STRAT:{sym}] 🚨🚨🚨 HARD SL TRIGGERED: pnl_bid={pnl_bps:.2f} bps (mid={pnl_bps_mid:.2f}) <= {HARD_SL_BPS}")
                        
                        # Get actual position qty
                        pos = await self._exec.get_position(sym)
                        actual_qty = float(pos.get("qty", 0.0))
                        
                        if actual_qty > 0:
                            # IMMEDIATE MARKET EXIT - NO LIMIT ATTEMPTS!
                            exit_result = await self._exec.place_market(
                                sym, "SELL", qty=actual_qty, tag="HARD_SL"
                            )
                            
                            if exit_result:
                                exit_price = exit_result.get("fill_price", bid)
                            else:
                                # Force flatten if market order failed
                                print(f"[STRAT:{sym}] 🚨 MARKET failed, forcing flatten")
                                await self._exec.flatten_symbol(sym)
                                exit_price = bid
                            
                            # Calculate REAL PnL after exit
                            real_pnl_bps = (exit_price - avg_entry) / avg_entry * 1e4 if avg_entry > 0 else 0.0
                            
                            print(f"[STRAT:{sym}] 🚨 HARD SL EXIT: {actual_qty:.6f} @ {exit_price:.6f} "
                                  f"(real_pnl={real_pnl_bps:.2f} bps, intended={pnl_bps:.2f} bps)")
                            
                            # Clean up state
                            in_pos = False
                            positions_list.clear()
                            last_exit_ts_ms = time.time() * 1000
                            st.last_exit_ts = int(last_exit_ts_ms)
                            st.hard_sl_triggered = True
                            
                            # Reset trailing stop state
                            st.trailing_active = False
                            st.trailing_stop_price = 0.0
                            st.peak_price = 0.0
                            
                            # Log metrics
                            if _METRICS_OK:
                                try:
                                    strategy_exits_total.labels(sym, "HARD_SL").inc()
                                    strategy_open_positions.labels(sym).set(0)
                                    pos2 = await self._exec.get_position(sym)
                                    strategy_realized_pnl_total.labels(sym).set(float(pos2.get("realized_pnl", 0.0)))
                                    strategy_trade_pnl_bps.labels(sym).observe(abs(float(real_pnl_bps)))
                                except Exception:
                                    pass
                            
                            # Log to ML logger
                            if st.current_trade_id:
                                try:
                                    from app.services.ml_trade_logger import get_ml_trade_logger
                                    ml_logger = get_ml_trade_logger()
                                    pnl_usd = (exit_price - avg_entry) * actual_qty if avg_entry > 0 else 0.0
                                    ml_logger.log_exit(
                                        symbol=sym,
                                        exit_price=exit_price,
                                        exit_qty=actual_qty,
                                        exit_reason="HARD_SL",
                                        pnl_usd=pnl_usd,
                                        pnl_bps=real_pnl_bps,
                                        pnl_percent=(exit_price - avg_entry) / avg_entry * 100 if avg_entry > 0 else 0.0,
                                        hold_duration_sec=elapsed_s,
                                        max_favorable_excursion_bps=None,
                                        max_adverse_excursion_bps=None,
                                        peak_price=None,
                                        lowest_price=None,
                                    )
                                except Exception:
                                    pass
                            
                            # Log to trade DB (non-blocking)
                            if st.current_trade_db_id:
                                trade_db_id = st.current_trade_db_id
                                async def _log_hard_sl():
                                    async with _db_semaphore:
                                        db = None
                                        try:
                                            db = SessionLocal()
                                            trade = db.query(Trade).filter(Trade.id == trade_db_id).first()
                                            if trade:
                                                trade.close_trade(
                                                    exit_time=datetime.fromtimestamp(time.time()),
                                                    exit_price=exit_price,
                                                    exit_qty=actual_qty,
                                                    exit_side="SELL",
                                                    exit_reason="HARD_SL",
                                                    exit_fee=0.0
                                                )
                                                db.commit()
                                        except Exception as e:
                                            print(f"[STRAT:{sym}] ⚠️ Failed to log HARD_SL exit: {e}")
                                        finally:
                                            if db:
                                                try:
                                                    db.close()
                                                except:
                                                    pass
                                asyncio.create_task(_log_hard_sl())
                                st.current_trade_db_id = None
                                st.current_trade_id = None
                        
                        await asyncio.sleep(poll_ms / 1000)
                        continue
                    # ═══════════════════════════════════════════════════════════

                    # ⏰ CHECK: Close before end of trading window
                    should_close_window, close_reason = self._should_close_before_end()
                    if should_close_window:
                        if not hasattr(st, '_last_window_close_log') or (now - st._last_window_close_log) > 10:
                            st._last_window_close_log = now
                            print(f"[STRAT:{sym}] ⏰ Closing before end: {close_reason}")
                        
                        # Force exit with market order
                        try:
                            pos = await self._exec.get_position(sym)
                            actual_qty = float(pos.get("qty", 0.0))
                            if actual_qty > 0:
                                await self._exec.place_market(sym, "SELL", qty=actual_qty, tag="mm_exit_window")
                                await self._exec.cancel_orders(sym)
                                await self._exec.flatten_symbol(sym)
                                
                                # ═══ PYRAMID: Remove closed position(s) ═══
                                positions_list.clear()  # Clear all positions after exit
                                last_exit_ts_ms = time.time() * 1000
                                st.last_exit_ts = int(last_exit_ts_ms)

                                print(f"[PYRAMID] {sym}: Closed all positions, remaining={len(positions_list)}")
                                
                                if _METRICS_OK:
                                    try:
                                        strategy_exits_total.labels(sym, "WINDOW_CLOSE").inc()
                                        strategy_open_positions.labels(sym).set(0)
                                    except Exception:
                                        pass
                                
                                print(f"[STRAT:{sym}] EXIT WINDOW_CLOSE qty={actual_qty:.6f} (pnl_bps={pnl_bps:.2f})")
                        except Exception as e:
                            print(f"[STRAT:{sym}] ⚠️ Failed to close before window: {e}")
                        
                        await asyncio.sleep(poll_ms / 1000)
                        continue

                    # ═══════════════════════════════════════════════════════════
                    # TRAILING STOP LOGIC
                    # ═══════════════════════════════════════════════════════════
                    if p.enable_trailing_stop:
                        # Activate trailing stop when profit reaches activation threshold
                        if not st.trailing_active and pnl_bps >= p.trailing_activation_bps:
                            st.trailing_active = True
                            st.peak_price = mid
                            st.trailing_stop_price = mid - (p.trailing_stop_bps / 1e4 * mid)
                            print(
                                f"[STRAT:{sym}] 🎯 Trailing Stop ACTIVATED: "
                                f"peak={mid:.6f}, trail={st.trailing_stop_price:.6f}, pnl={pnl_bps:.2f}"
                            )
                        
                        # Update trailing stop if new peak reached
                        elif st.trailing_active:
                            # Check if price moved up significantly (more than step_bps)
                            price_increase_bps = (mid - st.peak_price) / st.peak_price * 1e4 if st.peak_price > 0 else 0.0
                            if price_increase_bps >= p.trailing_step_bps:
                                st.peak_price = mid
                                st.trailing_stop_price = mid - (p.trailing_stop_bps / 1e4 * mid)
                                print(
                                    f"[STRAT:{sym}] 📈 Trailing Stop UPDATED: "
                                    f"peak={mid:.6f}, trail={st.trailing_stop_price:.6f}, pnl={pnl_bps:.2f}"
                                )
                    # ═══════════════════════════════════════════════════════════

                    # ✅ USE ACTUAL PARAMS (from exploration)
                    # Get params used for this trade (stored when position opened)
                    # For now, we'll use the current values (will improve later with per-trade storage)
                    # Standard exit conditions (use saved params!)
                    can_exit_by_timeout = elapsed_s >= st.trade_timeout_sec
                    can_exit_by_tp = (elapsed_s * 1000 >= p.min_hold_ms) and (pnl_bps >= st.trade_take_profit_bps)
                    can_exit_by_sl = (elapsed_s * 1000 >= p.min_hold_ms) and (pnl_bps <= st.trade_stop_loss_bps)
                    
                    # Trailing stop exit condition (NEW)
                    can_exit_by_trailing = False
                    if p.enable_trailing_stop and st.trailing_active:
                        can_exit_by_trailing = mid <= st.trailing_stop_price
                        if can_exit_by_trailing:
                            print(
                                f"[STRAT:{sym}] 🎯 Trailing Stop TRIGGERED: "
                                f"mid={mid:.6f} <= trail={st.trailing_stop_price:.6f}, pnl={pnl_bps:.2f}"
                            )

                    # optional depth guard on exit (mirror of entry)
                    depth_exit_ok = True
                    if p.enable_depth_check:
                        # exiting SELL → need enough bid depth to absorb
                        depth_exit_ok = (abs_bid_usd >= p.order_size_usd)

                    if (can_exit_by_tp or can_exit_by_sl or can_exit_by_timeout or can_exit_by_trailing) and depth_exit_ok:
                        # Determine exit reason with priority: TRAIL > TP > SL > TIMEOUT
                        if can_exit_by_trailing and st.trailing_active:
                            reason = "TRAIL"
                        elif can_exit_by_tp:
                            reason = "TP"
                        elif can_exit_by_sl:
                            reason = "SL"
                        else:
                            reason = "TIMEOUT"
                        
                        # ═══ GET ACTUAL POSITION QTY ═══
                        # Use real qty from position (handles partial fills)
                        try:
                            pos = await self._exec.get_position(sym)
                            actual_qty = float(pos.get("qty", 0.0))
                            if actual_qty <= 0:
                                print(f"[STRAT:{sym}] ⚠️ No position to exit (qty={actual_qty})")
                                in_pos = False
                                continue
                        except Exception as e:
                            print(f"[STRAT:{sym}] ⚠️ Failed to get position: {e}")
                            actual_qty = qty_units  # Fallback to requested qty
                        # ═══════════════════════════════
                        
                        # ═══════════════════════════════
                        # TP/TRAIL → LIMIT order (maker fee 0%)
                        # TIMEOUT/SL → MARKET order (taker fee 0.05%)
                        exit_oid = None
                        original_reason = reason  # Keep original reason
                        
                        if can_exit_by_tp or (can_exit_by_trailing and st.trailing_active):
                            exit_price = ask
                            exit_result = await self._exec.place_maker(sym, "SELL", price=exit_price, qty=actual_qty, tag="mm_exit_tp")
                            if not exit_result:
                                # ═══ CRITICAL FIX: Re-check PnL before MARKET fallback! ═══
                                # Price may have moved while waiting for LIMIT fill
                                q_new = await bt_service.get_quote(sym)
                                new_bid = float(q_new.get("bid", bid))
                                new_ask = float(q_new.get("ask", ask))
                                new_mid = (new_bid + new_ask) / 2 if new_bid > 0 and new_ask > 0 else mid
                                new_pnl_bps = (new_mid - avg_entry) / avg_entry * 1e4 if avg_entry > 0 else 0.0
                                
                                print(f"[STRAT:{sym}] ⚠️ {original_reason} LIMIT not filled. "
                                      f"Original pnl={pnl_bps:.2f}, new pnl={new_pnl_bps:.2f}")
                                
                                # Check if we've hit HARD SL while waiting
                                HARD_SL_BPS = -10.0
                                MIN_TP_FOR_MARKET = 1.0  # Min profit to use MARKET
                                
                                if new_pnl_bps <= HARD_SL_BPS:
                                    reason = "HARD_SL"
                                    print(f"[STRAT:{sym}] 🚨 Hit HARD SL while waiting for LIMIT!")
                                elif new_pnl_bps >= MIN_TP_FOR_MARKET:
                                    # Still in profit (at least 1 bps), use MARKET
                                    reason = f"{original_reason}_MARKET"
                                else:
                                    # Lost profit, exit anyway but mark correctly
                                    reason = f"{original_reason}_EXPIRED"
                                    print(f"[STRAT:{sym}] ⚠️ TP profit evaporated: was {pnl_bps:.2f}, now {new_pnl_bps:.2f}")
                                
                                # Update pnl_bps to reflect reality
                                pnl_bps = new_pnl_bps
                                
                                exit_result = await self._exec.place_market(sym, "SELL", qty=actual_qty, tag=f"mm_exit_{reason.lower()}")
                            if exit_result:
                                exit_price = exit_result.get("fill_price", exit_price)
                                exit_oid = exit_result.get("order_id")
                            else:
                                exit_oid = None
                        else:
                            # TIMEOUT or SL → use MARKET order
                            exit_result = await self._exec.place_market(sym, "SELL", qty=actual_qty, tag=f"mm_exit_{reason.lower()}")
                            if exit_result:
                                exit_price = exit_result.get("fill_price", bid)
                                exit_oid = exit_result.get("order_id")
                            else:
                                exit_price = bid
                                exit_oid = None
                        
                        # ═══ CRITICAL FIX (Jan 19, 2026): Recalculate REAL PnL after fill! ═══
                        # The pnl_bps calculated earlier used mid/bid estimate
                        # Now we have actual exit_price, recalculate for accurate logging
                        real_pnl_bps = (exit_price - avg_entry) / avg_entry * 1e4 if avg_entry > 0 else 0.0
                        
                        # Update reason if "TP" but actually lost money
                        if reason == "TP" and real_pnl_bps < -3.0:  # Lost more than 3 bps
                            reason = "TP_SLIPPAGE"
                            print(f"[STRAT:{sym}] ⚠️ TP became loss! Expected pnl={pnl_bps:.2f}, actual={real_pnl_bps:.2f} → {reason}")
                        
                        # Use REAL pnl for all logging
                        pnl_bps = real_pnl_bps
                        # ═══════════════════════════════════════════════════════════════════
                        
                        await self._exec.cancel_orders(sym)
                        
                        # Only flatten if exit order failed
                        if not exit_oid:
                            print(f"[STRAT:{sym}] ⚠️ Exit order failed, forcing flatten")
                            await self._exec.flatten_symbol(sym)

                        in_pos = False
                        last_exit_ts_ms = time.time() * 1000
                        st.last_exit_ts = int(last_exit_ts_ms)

                        st.trailing_active = False
                        st.trailing_stop_price = 0.0
                        st.peak_price = 0.0

                        if _METRICS_OK:
                            try:
                                strategy_exits_total.labels(sym, reason).inc()
                                strategy_open_positions.labels(sym).set(0)
                                pos2 = await self._exec.get_position(sym)
                                strategy_realized_pnl_total.labels(sym).set(float(pos2.get("realized_pnl", 0.0)))
                                strategy_trade_pnl_bps.labels(sym).observe(abs(float(pnl_bps)))
                                strategy_trade_duration_seconds.labels(sym).observe(max(0.0, float(elapsed_s)))
                            except Exception:
                                pass

                        print(
                            f"[STRAT:{sym}] EXIT SELL qty={qty_units:.6f} @ {exit_price} [{reason}] "
                            f"(pnl_bps={pnl_bps:.2f}, held={elapsed_s:.2f}s)"
                        )

                        # ═══ ML TRADE LOGGER: Log exit ═══
                        if st.current_trade_id:
                            try:
                                from app.services.ml_trade_logger import get_ml_trade_logger
                                ml_logger = get_ml_trade_logger()
                                
                                pnl_usd = (exit_price - entry_px) * actual_qty if entry_px > 0 else 0.0
                                pnl_percent = (exit_price - entry_px) / entry_px * 100 if entry_px > 0 else 0.0
                                
                                ml_logger.log_exit(
                                    symbol=sym,
                                    exit_price=exit_price,
                                    exit_qty=actual_qty,
                                    exit_reason=reason,
                                    pnl_usd=pnl_usd,
                                    pnl_bps=pnl_bps,
                                    pnl_percent=pnl_percent,
                                    hold_duration_sec=elapsed_s,
                                    max_favorable_excursion_bps=None,
                                    max_adverse_excursion_bps=None,
                                    peak_price=None,
                                    lowest_price=None,
                                )
                                
                                print(f"[ML_LOGGER] ✅ Exit logged: {st.current_trade_id}")
                                
                            except Exception as e:
                                print(f"[ML_LOGGER] ⚠️ Failed to log exit: {e}")
                        # ═════════════════════════════════

                        # ═══ LOGGING: Close trade ═══
                        if st.current_trade_db_id:
                            trade_db_id = st.current_trade_db_id
                            
                            async def _log_exit():
                                async with _db_semaphore:
                                    db = None
                                    try:
                                        db = SessionLocal()
                                        trade = db.query(Trade).filter(Trade.id == trade_db_id).first()
                                        if trade:
                                            trade.close_trade(
                                                exit_time=datetime.fromtimestamp(now),
                                                exit_price=exit_price,
                                                exit_qty=qty_units,
                                                exit_side="SELL",
                                                exit_reason=reason,
                                                exit_fee=0.0
                                            )
                                            db.commit()
                                    except Exception as e:
                                        print(f"[STRAT:{sym}] ⚠️ Failed to log exit: {e}")
                                    finally:
                                        if db:
                                            try:
                                                db.close()
                                            except:
                                                pass
                            
                            asyncio.create_task(_log_exit())
                            st.current_trade_db_id = None
                            st.current_trade_id = None
                        # ═════════════════════════════

                        if _METRICS_OK:
                            try:
                                strategy_exits_total.labels(sym, reason).inc()
                                strategy_open_positions.labels(sym).set(0)
                                pos2 = await self._exec.get_position(sym)
                                strategy_realized_pnl_total.labels(sym).set(float(pos2.get("realized_pnl", 0.0)))
                                strategy_trade_pnl_bps.labels(sym).observe(abs(float(pnl_bps)))
                                strategy_trade_duration_seconds.labels(sym).observe(max(0.0, float(elapsed_s)))
                            except Exception:
                                pass

                        # ═══════════════════════════════════════════════════════
                        # TRACK TRADE RESULT IN RISK MANAGER (NON-BLOCKING)
                        # ═══════════════════════════════════════════════════════
                        async def _track_result():
                            async with _db_semaphore:
                                try:
                                    risk_manager = get_risk_manager()
                                    pnl_usd = (exit_price - entry_px) * qty_units if entry_px > 0 else 0.0
                                    await risk_manager.track_trade_result(symbol=sym, pnl_usd=pnl_usd)
                                    print(f"[STRAT:{sym}] 📊 Trade tracked: pnl_usd=${pnl_usd:.2f}, win={pnl_usd > 0}")
                                except Exception as e:
                                    print(f"[STRAT:{sym}] ⚠️ Failed to track trade: {e}")

                        asyncio.create_task(_track_result())
                        # ═══════════════════════════════════════════════════════

                await asyncio.sleep(poll_ms / 1000)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            st = self._symbols.get(sym)
            if st:
                st.last_error = str(e)
            print(f"[STRAT:{sym}] ERROR: {e}")
        finally:
            if _METRICS_OK:
                try:
                    strategy_open_positions.labels(sym).set(0)
                    pos = await self._exec.get_position(sym)
                    strategy_realized_pnl_total.labels(sym).set(float(pos.get("realized_pnl", 0.0)))
                except Exception:
                    pass
                try:
                    strategy_symbols_running.dec()
                except Exception:
                    pass
            print(f"[STRAT:{sym}] loop stopped")

    # For external diagnostics / tuning endpoints
    def params(self) -> StrategyParams:
        return self._params

    def update_params(self, patch: dict) -> dict:
        """
        Patch StrategyParams with provided keys. Unknown keys are ignored.
        Returns the updated params as a dict.
        """
        if not isinstance(patch, dict):
            return asdict(self._params)

        allowed = {f.name for f in dc_fields(StrategyParams)}
        for k, v in patch.items():
            if k in allowed and v is not None:
                try:
                    setattr(self._params, k, v)
                except Exception:
                    pass
        # no cached snapshot used in loop, so hot-apply is immediate
        return asdict(self._params)