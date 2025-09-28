# app/api/routes.py
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


def _route_exists(root: APIRouter, path: str, method: str) -> bool:
    """
    Return True if a path+method is already registered on the router.
    """
    want_path = path.rstrip("/") or "/"
    want_method = method.upper()
    for r in getattr(root, "routes", []):
        r_path = (getattr(r, "path", None) or "").rstrip("/") or "/"
        methods = {m.upper() for m in getattr(r, "methods", set())}
        if r_path == want_path and want_method in methods:
            return True
    return False


def _mount_subrouters(root: APIRouter) -> None:
    """
    Import sub-routers lazily to minimize circular-import risk.
    Keep 'ui' mounted in main.py only (to avoid double-registering).
    """
    # Core routers
    from app.routers.market import router as market_router
    from app.routers.strategy import router as strategy_router
    from app.routers.execution import router as execution_router
    from app.routers.health import router as health_router
    from app.routers.account import router as account_router
    from app.routers.config import router as config_router  # ✅ mount config

    # New: PnL & Portfolio routers
    from app.routers.pnl import router as pnl_router
    from app.routers.portfolio import router as portfolio_router

    root.include_router(market_router)
    root.include_router(strategy_router)
    root.include_router(execution_router)
    root.include_router(health_router)
    root.include_router(account_router)
    root.include_router(config_router)      # /api/config/...
    root.include_router(pnl_router)         # /api/pnl/...
    root.include_router(portfolio_router)   # /api/portfolio/...

    # Stream router (SSE). Import last, and guard against duplicates.
    try:
        from app.api.stream import router as stream_router  # noqa: WPS433
    except Exception as e:  # pragma: no cover
        print(f"⚠️ Stream router not mounted: {e}")
    else:
        # Mount only if /api/market/stream [GET] is NOT already present
        if not _route_exists(root, "/api/market/stream", "GET"):
            root.include_router(stream_router)
        else:
            print("ℹ️ Skipping app.api.stream router: /api/market/stream already registered.")


# Mount everything once this module is imported
_mount_subrouters(router)

__all__ = ["router"]
