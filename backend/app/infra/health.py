# app/infra/health.py
from __future__ import annotations
import time
from typing import Dict, Optional

class WSHealthRegistry:
    """Простая регистратура состояния по символам."""
    def __init__(self) -> None:
        self._last_tick_ms: Dict[str, int] = {}
        self._last_lag_ms: Dict[str, int] = {}
        self._started_ms: Optional[int] = None

    def mark_started(self) -> None:
        self._started_ms = int(time.time() * 1000)

    def update(self, symbol: str, lag_ms: Optional[int]) -> None:
        now = int(time.time() * 1000)
        self._last_tick_ms[symbol] = now
        if lag_ms is not None:
            self._last_lag_ms[symbol] = lag_ms

    def snapshot(self) -> Dict[str, dict]:
        now = int(time.time() * 1000)
        out: Dict[str, dict] = {}
        for sym, ts in self._last_tick_ms.items():
            out[sym] = {
                "ms_since_last": max(0, now - ts),
                "last_lag_ms": self._last_lag_ms.get(sym),
            }
        return {
            "_meta": {
                "started_ms": self._started_ms,
                "uptime_ms": (now - self._started_ms) if self._started_ms else None,
            },
            "symbols": out,
        }

ws_health = WSHealthRegistry()
