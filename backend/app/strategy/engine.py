from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, asdict, fields as dc_fields
from typing import Dict, Optional, Protocol, List

from app.config.constants import (
    MIN_SPREAD_BPS,
    EDGE_FLOOR_BPS,
    ABSORPTION_X_BPS,
    MAX_CONCURRENT_SYMBOLS,
    ORDER_SIZE_USD,
    TIMEOUT_EXIT_SEC,
)
from app.services import book_tracker as bt_service
from app.services.book_tracker import ensure_symbols_subscribed

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


# ───────────────────────── Execution port contract ─────────────────────────
class ExecutionPort(Protocol):
    async def start_symbol(self, symbol: str) -> None: ...
    async def stop_symbol(self, symbol: str) -> None: ...
    async def flatten_symbol(self, symbol: str) -> None: ...
    async def cancel_orders(self, symbol: str) -> None: ...
    async def place_maker(self, symbol: str, side: str, price: float, qty: float, tag: str = "mm") -> Optional[str]: ...
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
    take_profit_bps: float = 2.0
    stop_loss_bps: float = -3.0
    min_hold_ms: int = 600
    reenter_cooldown_ms: int = 1000

    # Debug / testnet helper
    debug_force_entry: bool = False


# ───────────────────────────── Per-symbol state ────────────────────────────
@dataclass
class SymbolState:
    running: bool = False
    task: Optional[asyncio.Task] = None
    last_entry_ts: int = 0
    last_exit_ts: int = 0
    last_error: str = ""


# ───────────────────────────── Strategy engine ─────────────────────────────
class StrategyEngine:
    def __init__(self, exec_port: ExecutionPort, params: Optional[StrategyParams] = None) -> None:
        self._exec = exec_port
        self._params = params or StrategyParams()
        self._symbols: Dict[str, SymbolState] = {}
        self._lock = asyncio.Lock()

    # ───────── public operations ─────────
    async def start_symbols(self, symbols: List[str]) -> None:
        """Start trading for the given symbols (respecting max_concurrent_symbols)."""
        syms = [s.upper() for s in symbols if s and s.strip()]
        if not syms:
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
                if st.running and st.task and not st.task.done():
                    continue
                st.running = True
                st.task = asyncio.create_task(self._symbol_loop(sym))
                # Let the execution port warm any per-symbol state
                try:
                    await self._exec.start_symbol(sym)
                except Exception:
                    pass

                # seed metrics safely
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
                print(f"[STRAT] ⏹ stop {sym}")

    async def stop_all(self, flatten: bool = False) -> None:
        """Stop all active symbols."""
        await self.stop_symbols(list(self._symbols.keys()), flatten=flatten)

    # ───────── per-symbol loop ─────────
    async def _symbol_loop(self, symbol: str) -> None:
        p = self._params
        sym = symbol.upper()
        print(f"[STRAT:{sym}] loop started")

        if _METRICS_OK:
            try:
                strategy_symbols_running.inc()
            except Exception:
                pass

        poll_ms = 120
        in_pos = False
        entry_px = 0.0
        entry_ts = 0.0
        qty_units = 0.0
        last_exit_ts = 0.0  # local cooldown clock (ms)

        try:
            while True:
                st = self._symbols.get(sym)
                if not (st and st.running):
                    break

                q = await bt_service.get_quote(sym)
                bid = float(q.get("bid", 0.0))
                ask = float(q.get("ask", 0.0))
                mid = float(q.get("mid", 0.0))
                spread_bps = float(q.get("spread_bps", 0.0))
                imb = float(q.get("imbalance", 0.0))
                abs_bid_usd = float(q.get("absorption_bid_usd", 0.0))
                abs_ask_usd = float(q.get("absorption_ask_usd", 0.0))

                if bid <= 0.0 or ask <= 0.0 or mid <= 0.0:
                    await asyncio.sleep(poll_ms / 1000)
                    continue

                now = time.time()

                if not in_pos:
                    # re-enter cooldown
                    if (now * 1000 - last_exit_ts) < p.reenter_cooldown_ms:
                        await asyncio.sleep(poll_ms / 1000)
                        continue

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

                    # entry filters
                    base_ok = (spread_bps >= p.min_spread_bps) and (p.imbalance_min <= imb <= p.imbalance_max)
                    depth_ok = True
                    if p.enable_depth_check:
                        # we BUY → for later SELL, we want ask side to have capacity
                        depth_ok = (abs_ask_usd >= p.order_size_usd)
                    edge_ok = (spread_bps >= p.edge_floor_bps)

                    if base_ok and depth_ok and edge_ok:
                        qty_units = max(0.0, p.order_size_usd / bid)
                        if qty_units > 0.0:
                            oid = await self._exec.place_maker(sym, "BUY", price=bid, qty=qty_units, tag="mm_entry")
                            if oid:
                                in_pos = True
                                entry_px = bid
                                entry_ts = now
                                st.last_entry_ts = int(entry_ts * 1000)
                                if _METRICS_OK:
                                    try:
                                        strategy_entries_total.labels(sym).inc()
                                        strategy_open_positions.labels(sym).set(1)
                                        strategy_edge_bps_at_entry.labels(sym).observe(max(0.0, spread_bps))
                                    except Exception:
                                        pass
                                print(f"[STRAT:{sym}] ENTRY BUY qty={qty_units:.6f} @ {bid}")

                else:
                    elapsed_s = now - entry_ts
                    pnl_bps = (mid - entry_px) / entry_px * 1e4 if entry_px > 0 else 0.0

                    can_exit_by_timeout = elapsed_s >= p.timeout_exit_sec
                    can_exit_by_tp = (elapsed_s * 1000 >= p.min_hold_ms) and (pnl_bps >= p.take_profit_bps)
                    can_exit_by_sl = (elapsed_s * 1000 >= p.min_hold_ms) and (pnl_bps <= p.stop_loss_bps)

                    # optional depth guard on exit (mirror of entry)
                    depth_exit_ok = True
                    if p.enable_depth_check:
                        # exiting SELL → need enough bid depth to absorb
                        depth_exit_ok = (abs_bid_usd >= p.order_size_usd)

                    if (can_exit_by_tp or can_exit_by_sl or can_exit_by_timeout) and depth_exit_ok:
                        reason = "TP" if can_exit_by_tp else ("SL" if can_exit_by_sl else "TIMEOUT")
                        exit_price = ask if (can_exit_by_tp or can_exit_by_timeout) else bid

                        await self._exec.place_maker(sym, "SELL", price=exit_price, qty=qty_units, tag="mm_exit")
                        await self._exec.cancel_orders(sym)
                        await self._exec.flatten_symbol(sym)

                        in_pos = False
                        last_exit_ts = time.time() * 1000
                        st.last_exit_ts = int(last_exit_ts)

                        if _METRICS_OK:
                            try:
                                strategy_exits_total.labels(sym, reason).inc()
                                strategy_open_positions.labels(sym).set(0)
                                pos = await self._exec.get_position(sym)
                                strategy_realized_pnl_total.labels(sym).set(float(pos.get("realized_pnl", 0.0)))
                                strategy_trade_pnl_bps.labels(sym).observe(abs(float(pnl_bps)))
                                strategy_trade_duration_seconds.labels(sym).observe(max(0.0, float(elapsed_s)))
                            except Exception:
                                pass

                        print(
                            f"[STRAT:{sym}] EXIT SELL qty={qty_units:.6f} @ {exit_price} [{reason}] "
                            f"(pnl_bps={pnl_bps:.2f}, held={elapsed_s:.2f}s)"
                        )

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
        return asdict(self._params)
