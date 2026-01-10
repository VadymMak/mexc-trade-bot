# app/routers/allocation.py
from __future__ import annotations

from typing import Dict, Any, List

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from app.db.session import SessionLocal
from app.services import allocation_manager

# Import engine and params from strategy router
from app.routers.strategy import _engine, get_params as get_strategy_params

router = APIRouter(prefix="/api/allocation", tags=["allocation"])


# ─────────────── Request/Response Models ───────────────

class AllocationModeRequest(BaseModel):
    mode: str  # "dynamic" or "equal"


class AllocationModeResponse(BaseModel):
    mode: str


class AllocationCalculateResponse(BaseModel):
    mode: str
    total_capital: float
    position_size_usd: float
    max_positions: int
    active_symbols: List[str]
    allocations: Dict[str, Dict[str, Any]]


# ─────────────── Endpoints ───────────────

@router.get("/mode", response_model=AllocationModeResponse)
async def get_allocation_mode() -> AllocationModeResponse:
    """
    Get current allocation mode.
    
    Returns:
        - mode: "dynamic" or "equal"
    """
    mode = allocation_manager.get_allocation_mode()
    return AllocationModeResponse(mode=mode)


@router.post("/mode", response_model=AllocationModeResponse)
async def set_allocation_mode(
    payload: AllocationModeRequest = Body(...)
) -> AllocationModeResponse:
    """
    Set allocation mode.
    
    Body:
        - mode: "dynamic" or "equal"
    """
    mode = payload.mode.lower().strip()
    
    if mode not in {"dynamic", "equal", "smart"}:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{mode}'. Must be 'dynamic', 'equal', or 'smart'"
        )
    
    new_mode = allocation_manager.set_allocation_mode(mode)
    return AllocationModeResponse(mode=new_mode)


@router.get("/calculate", response_model=AllocationCalculateResponse)
async def calculate_allocation() -> AllocationCalculateResponse:
    """
    Calculate allocation for active symbols.
    
    Reads:
        - Current mode (dynamic or equal)
        - Risk limits (total capital, max positions)
        - Strategy params (position size)
        - Active symbols from strategy engine
    
    Returns allocation breakdown per symbol.
    """
    # Get current mode
    mode = allocation_manager.get_allocation_mode()
    
    # Get risk limits
    from app.strategy.risk import get_risk_manager
    risk_manager = get_risk_manager()
    limits = risk_manager.get_limits()
    total_capital = limits.get("account_balance_usd", 1000.0)
    max_positions_total = limits.get("max_positions", 5)
        
    # Get strategy params
    params = await get_strategy_params()
    position_size_usd = params.get("order_size_usd", 50.0)
    
    # Get active symbols from engine's internal state
    active_symbols = []
    try:
        for sym, state in _engine._symbols.items():
            if state.running:
                active_symbols.append(sym)
    except Exception as e:
        print(f"[ALLOCATION] Warning: Could not get active symbols: {e}")
    
    if not active_symbols:
        # No symbols active, return empty
        return AllocationCalculateResponse(
            mode=mode,
            total_capital=total_capital,
            position_size_usd=position_size_usd,
            max_positions=max_positions_total,
            active_symbols=[],
            allocations={}
        )
    
    # Calculate allocation
    db = SessionLocal()
    try:
        allocations = allocation_manager.calculate_allocation(
            symbols=active_symbols,
            total_capital=total_capital,
            position_size_usd=position_size_usd,
            mode=mode,
            db=db
        )
        
        return AllocationCalculateResponse(
            mode=mode,
            total_capital=total_capital,
            position_size_usd=position_size_usd,
            max_positions=max_positions_total,
            active_symbols=active_symbols,
            allocations=allocations
        )
    finally:
        db.close()