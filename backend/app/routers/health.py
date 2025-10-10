# app/routers/health.py
from __future__ import annotations

from fastapi import APIRouter, Response, status
from app.config.settings import settings
from app.infra import metrics as m  # uses our Prometheus gauges/counters

router = APIRouter(prefix="/api", tags=["health"])


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
        "symbols": settings.symbols,
        "enable_ws": bool(settings.enable_ws),
        "ui_id": settings.ui_id,
    }


def _gauge_value_safe(gauge) -> float:
    """
    Prometheus client stores the current value in a private struct.
    If the metric hasn't been set yet, return 0.0.
    """
    try:
        # Gauge._value is a prometheus_client.core._ValueClass
        return float(gauge._value.get())  # type: ignore[attr-defined]
    except Exception:
        return 0.0


@router.get("/healthz")
async def healthz():
    """
    Lightweight health endpoint for UI/monitoring.
    Uses Prometheus gauges if present; falls back to 0.0.
    Returns 200 when healthy, 503 with warnings when something looks off.
    """
    ws_lag_ms = _gauge_value_safe(m.ws_lag_ms)
    ws_subs = _gauge_value_safe(m.ws_active_subscriptions)
    strat_loops = _gauge_value_safe(m.strategy_symbols_running)
    scan_cands = _gauge_value_safe(m.scanner_candidates)

    warnings: list[str] = []

    # Warn if WS lag is too high
    if ws_lag_ms and ws_lag_ms > settings.health_ws_lag_ms_warn:
        warnings.append(f"ws_lag_ms>{settings.health_ws_lag_ms_warn}")

    # If WS is enabled, but no subs yet, warn (harmless during cold start)
    if settings.enable_ws and ws_subs == 0:
        warnings.append("no_active_ws_subscriptions")

    # Basic LIVE safety (keys/database)
    for issue in settings.explain_sanity():
        warnings.append(f"sanity:{issue}")

    payload = {
        "ok": len(warnings) == 0,
        "provider": settings.active_provider,
        "mode": settings.active_mode,
        "symbols": settings.symbols,
        "ws": {
            "lag_ms": ws_lag_ms,
            "active_subscriptions": ws_subs,
        },
        "strategy": {
            "symbols_running": strat_loops,
        },
        "scanner": {
            "candidates": scan_cands,
        },
        "warnings": warnings,
    }

    code = status.HTTP_200_OK if not warnings else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(content=__import__("json").dumps(payload),
                    media_type="application/json",
                    status_code=code)


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
