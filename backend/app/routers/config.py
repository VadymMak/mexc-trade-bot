# app/routers/config.py
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.services.config_manager import config_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])


# ──────────────────────────── Schemas ────────────────────────────
class ProviderState(BaseModel):
    active: str = Field(..., description="Current provider: gate | mexc | binance")
    mode: str = Field(..., description="Mode: PAPER | DEMO | LIVE")
    available: list[str] = Field(..., description="List of available providers")
    ws_enabled: bool = Field(..., description="Whether WS is enabled for the active provider")
    revision: int = Field(..., description="Incremented each successful switch")


class ProviderSwitchIn(BaseModel):
    provider: str = Field(..., description="Target provider: gate | mexc | binance")
    mode: str = Field(..., description="Target mode: PAPER | DEMO | LIVE")


# ─────────────────────────── Debug endpoint ───────────────────────
@router.get("/__debug", summary="Config router debug")
async def _config_debug():
    return {"ok": True}


# ─────────────────────────── Endpoints ───────────────────────────
@router.get("/provider", response_model=ProviderState, summary="Get active provider/mode")
async def get_provider_config() -> ProviderState:
    """
    Returns current provider configuration and readiness flags.
    """
    try:
        logger.info("GET /api/config/provider hit")  # visibility
        state = config_manager.get_state()  # expected dict-like with keys of ProviderState
        return ProviderState(**state)
    except Exception as e:
        logger.exception("config/get_provider failed: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")


@router.post(
    "/provider",
    response_model=ProviderState,
    summary="Switch active provider/mode (idempotent with X-Idempotency-Key)",
)
async def switch_provider_config(
    payload: ProviderSwitchIn,
    x_idempotency_key: Optional[str] = Header(default=None, convert_underscores=True, alias="X-Idempotency-Key"),
) -> ProviderState:
    """
    Safely switches provider/mode:
      1) stop strategies
      2) stop streams
      3) reset book tracker
      4) start streams for new provider/mode
      5) bump revision

    Idempotent when X-Idempotency-Key is provided.
    """
    try:
        # Normalize here; ConfigManager may also validate
        provider = payload.provider.strip().lower()
        mode = payload.mode.strip().upper()

        state = await config_manager.switch(
            provider=provider,
            mode=mode,
            idempotency_key=x_idempotency_key,
        )
        return ProviderState(**state)
    except ValueError as ve:
        # validation errors from ConfigManager (unsupported provider/mode)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(ve))
    except Exception as e:
        logger.exception("config/switch_provider failed: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")
