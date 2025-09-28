from __future__ import annotations

from fastapi import APIRouter
from app.config.settings import settings

router = APIRouter(prefix="/api", tags=["health"])

@router.get("/ping")
async def ping() -> dict:
    return {"ok": True}

@router.get("/info")
async def info() -> dict:
    return {
        "provider": (settings.exchange_provider or "").upper(),
        "mode": (settings.account_mode or "").upper(),
        "symbols": settings.symbols,
        "enable_ws": bool(settings.enable_ws),
        "binance_rest_base": settings.binance_rest_base,
    }
