# app/api/routes.py
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


def _mount_subrouters(root: APIRouter) -> None:
    """
    Import sub-routers lazily to minimize circular-import risk.
    Keep 'ui' mounted in main.py only (to avoid double-registering).
    """
    # Core routers
    from app.routers.market import router as market_router
    from app.routers.strategy import router as strategy_router
    from app.routers.execution import router as execution_router

    root.include_router(market_router)
    root.include_router(strategy_router)
    root.include_router(execution_router)

    # Stream router (SSE/WebSocket/etc.). Import last, and guarded.
    # If your stream module ever imports `api.routes`, a circular import will happen.
    # This late/guarded import reduces the chance of a startup failure.
    try:
        from app.api.stream import router as stream_router  # noqa: WPS433
    except Exception as e:  # pragma: no cover
        # Avoid failing app startup due to stream issues; you can log if you prefer.
        print(f"⚠️ Stream router not mounted: {e}")
    else:
        root.include_router(stream_router)


# Mount everything once this module is imported
_mount_subrouters(router)

__all__ = ["router"]
