# app/routers/portfolio.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


# ─────────────────────────────── Schemas ───────────────────────────────

class PortfolioAsset(BaseModel):
    asset: str = Field(..., description="Asset code, e.g. USDT, ETH")
    free: float = Field(..., description="Free balance")
    locked: float = Field(..., description="Locked balance (open orders etc.)")
    usd_eq: float = Field(..., description="USDT(eq) estimation")
    exchange: str = Field(..., description="Exchange name, e.g. MEXC, GATE")
    account_id: str = Field(..., description="Account identifier / subaccount if applicable")


# ─────────────────────────────── Helpers ───────────────────────────────

_STABLES = {"USDT", "USDC", "FDUSD", "BUSD"}


def _is_stable(asset: str) -> bool:
    return asset.upper() in _STABLES


def _usd_eq(asset: str, qty: float, price_map: Optional[Dict[str, float]] = None) -> float:
    """
    Very simple normalization:
    - Stablecoins: 1:1
    - Else: if a price_map is provided (e.g., {"ETHUSDT": 3500}), use qty*price
    - Else: 0 (caller can enrich later)
    """
    if qty == 0:
        return 0.0
    if _is_stable(asset):
        return float(qty)
    if price_map:
        # Prefer direct pair like ETHUSDT or asset-USDT if provided
        sym1 = f"{asset.upper()}USDT"
        if sym1 in price_map:
            return float(qty * price_map[sym1])
    return 0.0


def _fetch_balances_via_service() -> List[Dict[str, Any]]:
    """
    Try to get balances from your internal service layer.
    We attempt several likely entry points and return a unified list:
      [{ "asset": "USDT", "free": 123.4, "locked": 0.0, "exchange": "MEXC", "account_id": "acc1" }, ...]
    """
    # Pattern 1: a singleton/manager that exposes .get_balances()
    try:
        from app.services.exchange_private import get_balances as svc_get_balances  # type: ignore
        balances = svc_get_balances()
        if isinstance(balances, list):
            return balances  # expected to be list[dict]
    except Exception:
        pass

    # Pattern 2: provider-specific client exposing .balances() or similar
    try:
        from app.services.gate_private import get_balances as gate_get_balances  # type: ignore
        balances = gate_get_balances()
        if isinstance(balances, list):
            return balances
    except Exception:
        pass

    # If neither is available, let the caller handle 501
    return []


# ─────────────────────────────── Route ────────────────────────────────

@router.get("/assets", response_model=List[PortfolioAsset])
def get_portfolio_assets(
    # Optional future expansion: t=spot|futures etc.
    t: str = Query("spot", description="Account type (reserved)"),
    db: Session = Depends(get_db),
):
    """
    Return wallet balances normalized into USDT(eq).
    This proxies your existing balance source (services/exchange_private or gate_private).
    """
    raw = _fetch_balances_via_service()
    if not raw:
        # If your /api/account/balances is implemented, consider importing its internal
        # function instead of calling over HTTP. For now we return 501 with guidance.
        raise HTTPException(
            status_code=501,
            detail="No balance source found. Wire app.services.exchange_private.get_balances() to enable /api/portfolio/assets.",
        )

    # Optional: price map for non-stables (future extension — inject from quote tracker if desired)
    price_map: Dict[str, float] = {}

    out: List[PortfolioAsset] = []
    for item in raw:
        # Be defensive with keys
        asset = str(item.get("asset") or item.get("currency") or "").upper()
        free = float(item.get("free") or item.get("available") or 0.0)
        locked = float(item.get("locked") or item.get("frozen") or 0.0)
        exchange = str(item.get("exchange") or item.get("provider") or "N/A")
        account_id = str(item.get("account_id") or item.get("account") or "default")

        usd_eq = _usd_eq(asset, free + locked, price_map=price_map)

        out.append(
            PortfolioAsset(
                asset=asset,
                free=free,
                locked=locked,
                usd_eq=usd_eq,
                exchange=exchange,
                account_id=account_id,
            )
        )

    return out
