# app/routers/config.py
from __future__ import annotations

import logging
from typing import Optional, Literal

from fastapi import APIRouter, Header, HTTPException, status, Depends
from pydantic import BaseModel, Field

from app.services.config_manager import config_manager
from app.db.session import get_db  # yields SQLAlchemy Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

# Canonical literals used both for request & response typing
ProviderLiteral = Literal["gate", "mexc", "binance"]
ModeLiteral = Literal["PAPER", "DEMO", "LIVE"]


# ──────────────────────────── Schemas ────────────────────────────
class ProviderState(BaseModel):
    active: ProviderLiteral = Field(..., description="Current provider")
    mode: ModeLiteral = Field(..., description="Mode")
    available: list[ProviderLiteral] = Field(
        default_factory=lambda: ["gate", "mexc", "binance"],
        description="List of available providers",
    )
    ws_enabled: bool = Field(..., description="Whether WS is enabled for the active provider")
    revision: int = Field(..., description="Incremented each successful switch")


class ProviderSwitchIn(BaseModel):
    provider: ProviderLiteral = Field(..., description="Target provider")
    mode: ModeLiteral = Field(..., description="Target mode")


# ─────────────────────────── Debug endpoint ───────────────────────
@router.get("/__debug", summary="Config router debug")
async def _config_debug():
    """Quick health for the router itself."""
    return {"ok": True}


# ─────────────────────────── Endpoints ───────────────────────────
@router.get("/provider", response_model=ProviderState, summary="Get active provider/mode")
async def get_provider_config() -> ProviderState:
    """
    Return current provider configuration and readiness flags.
    Uses ConfigManager.state_for_api() to guarantee response shape.
    """
    try:
        logger.info("GET /api/config/provider")
        state = config_manager.state_for_api()
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
    db=Depends(get_db),
) -> ProviderState:
    """
    Safely switch provider/mode:
      1) stop strategies
      2) stop streams
      3) reset book tracker
      4) start streams for new provider/mode
      5) bump revision

    Idempotent when X-Idempotency-Key is provided (recent cached result returned).
    """
    try:
        provider = payload.provider.strip().lower()
        mode = payload.mode.strip().upper()

        state = await config_manager.switch(
            provider=provider,
            mode=mode,
            idempotency_key=x_idempotency_key,
            db=db,
        )
        return ProviderState(**state)
    except ValueError as ve:
        # validation errors from ConfigManager (unsupported provider/mode)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(ve))
    except Exception as e:
        logger.exception("config/switch_provider failed: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")
