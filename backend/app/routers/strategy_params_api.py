"""
New API endpoints for live parameter updates
Add to app/routers/strategy.py
"""
from fastapi import APIRouter, HTTPException, Header
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/strategy", tags=["strategy"])

# ═══════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════

class MLSettings(BaseModel):
    """ML filter settings"""
    enabled: bool = Field(description="Enable/disable ML filter")
    min_confidence: float = Field(ge=0.0, le=1.0, description="Minimum confidence threshold (0.0-1.0)")
    use_as_filter: bool = Field(description="Use ML as filter (vs weight)")
    weight: float = Field(ge=0.0, le=1.0, description="ML weight in combined score (0.0-1.0)")

class EntryFilters(BaseModel):
    """Entry filter parameters"""
    min_spread_bps: float = Field(ge=0.0, description="Minimum spread in bps")
    edge_floor_bps: float = Field(description="Minimum edge in bps")
    imbalance_min: float = Field(ge=0.0, le=1.0, description="Min imbalance (0.0-1.0)")
    imbalance_max: float = Field(ge=0.0, le=1.0, description="Max imbalance (0.0-1.0)")

class ExitParams(BaseModel):
    """Exit parameters"""
    take_profit_bps: float = Field(gt=0.0, description="Take profit in bps")
    stop_loss_bps: float = Field(lt=0.0, description="Stop loss in bps (negative)")
    timeout_exit_sec: int = Field(gt=0, description="Timeout in seconds")
    min_hold_ms: int = Field(ge=0, description="Minimum hold time in ms")

class RiskParams(BaseModel):
    """Risk management parameters"""
    order_size_usd: float = Field(gt=0.0, description="Order size in USD")
    max_exposure_usd: float = Field(gt=0.0, description="Max total exposure")
    max_per_symbol_usd: float = Field(gt=0.0, description="Max per symbol")
    min_seconds_between_trades: int = Field(ge=0, description="Cooldown in seconds")

class StrategyParamsUpdate(BaseModel):
    """Full strategy parameters update"""
    ml: Optional[MLSettings] = None
    entry: Optional[EntryFilters] = None
    exit: Optional[ExitParams] = None
    risk: Optional[RiskParams] = None

# ═══════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/params/full")
async def get_full_params() -> Dict[str, Any]:
    """
    Get all current strategy parameters including ML settings.
    
    Returns structured object with:
    - ml: ML filter settings
    - entry: Entry filter parameters
    - exit: Exit parameters
    - risk: Risk management
    """
    from app.strategy.engine import get_strategy_service
    
    service = get_strategy_service()
    params = service.get_params()
    
    # Get ML predictor settings
    from app.services.ml_predictor import get_ml_predictor
    ml_predictor = get_ml_predictor()
    ml_stats = ml_predictor.get_stats()
    
    return {
        "ml": {
            "enabled": ml_stats["enabled"],
            "min_confidence": ml_stats["min_confidence"],
            "weight": ml_stats["weight"],
            "use_as_filter": True,  # From params
            "model_version": ml_stats["model_version"],
            "predictions_count": ml_stats["predictions_count"],
        },
        "entry": {
            "min_spread_bps": params.min_spread_bps,
            "edge_floor_bps": params.edge_floor_bps,
            "imbalance_min": params.imbalance_min,
            "imbalance_max": params.imbalance_max,
        },
        "exit": {
            "take_profit_bps": params.take_profit_bps,
            "stop_loss_bps": params.stop_loss_bps,
            "timeout_exit_sec": params.timeout_exit_sec,
            "min_hold_ms": params.min_hold_ms,
        },
        "risk": {
            "order_size_usd": params.order_size_usd,
            "max_exposure_usd": params.max_exposure_usd,
            "max_per_symbol_usd": params.max_per_symbol_usd,
            "min_seconds_between_trades": params.min_seconds_between_trades,
        },
        "revision": params.revision if hasattr(params, 'revision') else 0,
    }

@router.patch("/params/ml")
async def update_ml_settings(
    settings: MLSettings,
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    """
    Update ML filter settings without restarting backend.
    
    Can toggle ML on/off and adjust confidence threshold in real-time.
    """
    from app.services.ml_predictor import get_ml_predictor
    
    ml = get_ml_predictor()
    
    # Update settings
    ml.enabled = settings.enabled
    ml.min_confidence = settings.min_confidence
    ml.weight = settings.weight
    
    return {
        "status": "success",
        "ml": {
            "enabled": ml.enabled,
            "min_confidence": ml.min_confidence,
            "weight": ml.weight,
            "model_version": ml.model_version,
        },
        "message": f"ML filter {'enabled' if settings.enabled else 'disabled'}",
    }

@router.patch("/params/entry")
async def update_entry_filters(
    filters: EntryFilters,
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    """
    Update entry filter parameters without restarting.
    """
    from app.strategy.engine import get_strategy_service
    
    service = get_strategy_service()
    params = service.get_params()
    
    # Update
    params.min_spread_bps = filters.min_spread_bps
    params.edge_floor_bps = filters.edge_floor_bps
    params.imbalance_min = filters.imbalance_min
    params.imbalance_max = filters.imbalance_max
    
    service.update_params(params)
    
    return {
        "status": "success",
        "entry": {
            "min_spread_bps": params.min_spread_bps,
            "edge_floor_bps": params.edge_floor_bps,
            "imbalance_min": params.imbalance_min,
            "imbalance_max": params.imbalance_max,
        },
        "message": "Entry filters updated",
    }

@router.patch("/params/exit")
async def update_exit_params(
    exit_params: ExitParams,
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    """
    Update exit parameters without restarting.
    """
    from app.strategy.engine import get_strategy_service
    
    service = get_strategy_service()
    params = service.get_params()
    
    # Update
    params.take_profit_bps = exit_params.take_profit_bps
    params.stop_loss_bps = exit_params.stop_loss_bps
    params.timeout_exit_sec = exit_params.timeout_exit_sec
    params.min_hold_ms = exit_params.min_hold_ms
    
    service.update_params(params)
    
    return {
        "status": "success",
        "exit": {
            "take_profit_bps": params.take_profit_bps,
            "stop_loss_bps": params.stop_loss_bps,
            "timeout_exit_sec": params.timeout_exit_sec,
            "min_hold_ms": params.min_hold_ms,
        },
        "message": "Exit parameters updated",
    }

@router.patch("/params/risk")
async def update_risk_params(
    risk: RiskParams,
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    """
    Update risk management parameters without restarting.
    """
    from app.strategy.engine import get_strategy_service
    
    service = get_strategy_service()
    params = service.get_params()
    
    # Update
    params.order_size_usd = risk.order_size_usd
    params.max_exposure_usd = risk.max_exposure_usd
    params.max_per_symbol_usd = risk.max_per_symbol_usd
    params.min_seconds_between_trades = risk.min_seconds_between_trades
    
    service.update_params(params)
    
    return {
        "status": "success",
        "risk": {
            "order_size_usd": params.order_size_usd,
            "max_exposure_usd": params.max_exposure_usd,
            "max_per_symbol_usd": params.max_per_symbol_usd,
            "min_seconds_between_trades": params.min_seconds_between_trades,
        },
        "message": "Risk parameters updated",
    }

@router.post("/params/reset")
async def reset_params_to_default(
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    """
    Reset all parameters to default values.
    """
    from app.strategy.engine import get_strategy_service, StrategyParams
    
    service = get_strategy_service()
    default_params = StrategyParams()  # Default values
    
    service.update_params(default_params)
    
    return {
        "status": "success",
        "message": "Parameters reset to defaults",
    }