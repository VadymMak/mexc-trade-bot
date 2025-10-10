# app/infra/health.py
from __future__ import annotations

import time
from typing import Dict, Optional

# Try to surface latest lag to Prometheus if metrics are wired up.
try:
    from app.infra.metrics import ws_lag_ms  # Gauge
except Exception:  # soft-fail if metrics module not present
    ws_lag_ms = None  # type: ignore


class WSHealthRegistry:
    """Lightweight WS health registry with backward-compatible API.

    Exposes:
      • mark_started()  – called when a WS loop starts
      • mark_tick()     – called on any parsed WS event (optionally per symbol + lag)
      • mark_stopped()  – called when the WS loop stops
      • update()        – alias to mark_tick(symbol, lag_ms) for backward compat
      • snapshot()      – structured state for /healthz or debugging
    """

    def __init__(self) -> None:
        self._last_tick_ms_by_symbol: Dict[str, int] = {}
        self._last_lag_ms_by_symbol: Dict[str, int] = {}

        self._started_ms: Optional[int] = None
        self._stopped_ms: Optional[int] = None

        # Global (any-symbol) activity markers, useful when caller doesn’t pass a symbol.
        self._last_any_tick_ms: Optional[int] = None
        self._last_any_lag_ms: Optional[int] = None

    # ───────── lifecycle ─────────

    def mark_started(self) -> None:
        self._started_ms = int(time.time() * 1000)
        self._stopped_ms = None

    def mark_stopped(self) -> None:
        self._stopped_ms = int(time.time() * 1000)

    # ───────── ticks / lag ─────────

    def mark_tick(self, symbol: Optional[str] = None, lag_ms: Optional[int] = None) -> None:
        """
        Record that we observed a WS tick "now". Optionally attach a symbol and measured lag.
        Compatible with callers that provide no args.
        """
        now = int(time.time() * 1000)
        self._last_any_tick_ms = now

        if lag_ms is not None:
            self._last_any_lag_ms = int(lag_ms)
            # Surface latest lag to Prometheus gauge if available
            try:
                if ws_lag_ms is not None:
                    ws_lag_ms.set(float(lag_ms))
            except Exception:
                pass

        if symbol:
            self._last_tick_ms_by_symbol[symbol] = now
            if lag_ms is not None:
                self._last_lag_ms_by_symbol[symbol] = int(lag_ms)

    # Backward-compat alias (older code used update(symbol, lag_ms))
    def update(self, symbol: str, lag_ms: Optional[int]) -> None:
        self.mark_tick(symbol=symbol, lag_ms=lag_ms)

    # ───────── snapshots ─────────

    def snapshot(self) -> Dict[str, dict]:
        now = int(time.time() * 1000)
        symbols: Dict[str, dict] = {}
        for sym, ts in self._last_tick_ms_by_symbol.items():
            symbols[sym] = {
                "ms_since_last": max(0, now - ts),
                "last_lag_ms": self._last_lag_ms_by_symbol.get(sym),
            }

        return {
            "_meta": {
                "started_ms": self._started_ms,
                "stopped_ms": self._stopped_ms,
                "uptime_ms": (now - self._started_ms) if self._started_ms else None,
                "ms_since_last_any": (max(0, now - self._last_any_tick_ms) if self._last_any_tick_ms else None),
                "last_any_lag_ms": self._last_any_lag_ms,
            },
            "symbols": symbols,
        }


ws_health = WSHealthRegistry()
