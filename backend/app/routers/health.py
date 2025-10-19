# app/routers/health.py
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.infra import metrics as m  # Prometheus gauges/counters (optional fields handled)

# ↓ helper & cache import for hit-rate display
#    if your helper lives elsewhere, adjust the import path accordingly.
from app.services.market_scanner import get_cache_hitrate  # existing helper
from app.services.candles_cache import candles_cache       # to show cache size

router = APIRouter(prefix="/api", tags=["health"])
log = logging.getLogger(__name__)


@router.get("/ping")
async def ping() -> dict:
    return {"ok": True}


@router.get("/info")
async def info() -> dict:
    return {
        # legacy/raw envs
        "env_provider": (settings.exchange_provider or "").upper(),
        "env_mode": (settings.account_mode or "").upper(),
        # resolved (what the app actually uses)
        "active_provider": settings.active_provider,
        "active_mode": settings.active_mode,
        # endpoints currently in effect
        "rest_base_url_resolved": settings.rest_base_url_resolved,
        "ws_base_url_resolved": settings.ws_base_url_resolved,
        # other useful bits
        "symbols": settings.symbols_unique,
        "enable_ws": bool(settings.enable_ws),
        "ui_id": settings.ui_id,
    }


def _gauge_value_safe(metric_obj: Any) -> float:
    """
    Return current value of a Prometheus Gauge/Counter if available; else 0.0.
    """
    try:
        # prometheus_client uses a private value holder; read defensively
        return float(metric_obj._value.get())  # type: ignore[attr-defined]
    except Exception:
        return 0.0


def _metric_or_zero(name: str) -> float:
    return _gauge_value_safe(getattr(m, name, None))


@router.get("/healthz")
async def healthz():
    """
    Lightweight health endpoint for UI/monitoring.
    Uses Prometheus gauges if present; falls back to 0.0.
    Returns 200 when healthy, 503 with warnings when something looks off.
    """
    ws_lag_ms = _metric_or_zero("ws_lag_ms")
    ws_subs = _metric_or_zero("ws_active_subscriptions")
    strat_loops = _metric_or_zero("strategy_symbols_running")
    scan_cands = _metric_or_zero("scanner_candidates")

    # Optional extras if your metrics module exports them
    ticks_per_sec = _metric_or_zero("ticks_per_sec")
    depth_updates_per_sec = _metric_or_zero("depth_updates_per_sec")
    last_tick_ts = _metric_or_zero("last_tick_ts")  # unix seconds if you expose it
    uptime_sec = _metric_or_zero("process_uptime_sec")

    # Candles cache hit-rate (via helper), plus simple cache size visibility
    hr: Optional[float] = None
    try:
        hr = get_cache_hitrate()
    except Exception:
        hr = None
    if hr is None:
        # best-effort fallback to property if helper couldn't fetch it
        try:
            hr = float(getattr(candles_cache, "hitrate", None))
        except Exception:
            hr = None
    try:
        candles_keys = len(getattr(candles_cache, "_candles", {}))
    except Exception:
        candles_keys = 0

    warnings: list[str] = []

    # Warn if WS lag is too high
    if ws_lag_ms and ws_lag_ms > settings.health_ws_lag_ms_warn:
        warnings.append(f"ws_lag_ms>{settings.health_ws_lag_ms_warn}")

    # If WS is enabled, but no subs yet, warn (harmless during cold start)
    if settings.enable_ws and ws_subs == 0:
        warnings.append("no_active_ws_subscriptions")

    # Basic LIVE safety (keys/database)
    warnings.extend([f"sanity:{issue}" for issue in settings.explain_sanity()])

    payload = {
        "ok": len(warnings) == 0,
        "provider": settings.active_provider,
        "mode": settings.active_mode,
        "symbols": settings.symbols_unique,
        "ws": {
            "lag_ms": ws_lag_ms,
            "active_subscriptions": ws_subs,
            "ticks_per_sec": ticks_per_sec,
            "depth_updates_per_sec": depth_updates_per_sec,
            "last_tick_ts": last_tick_ts,
            "lag_warn_threshold_ms": settings.health_ws_lag_ms_warn,
        },
        "strategy": {
            "symbols_running": strat_loops,
        },
        "scanner": {
            "candidates": scan_cands,
        },
        "cache": {
            "candles_hitrate": hr,     # None until a few requests warm the cache
            "candles_series": candles_keys,
        },
        "process": {
            "uptime_sec": uptime_sec,
        },
        "warnings": warnings,
    }

    code = status.HTTP_200_OK if not warnings else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=code)


@router.get("/sanity")
async def sanity() -> dict:
    """
    Explicit config sanity report (always 200; UI can surface these).
    """
    return {
        "issues": settings.explain_sanity(),
        "active_provider": settings.active_provider,
        "active_mode": settings.active_mode,
    }
