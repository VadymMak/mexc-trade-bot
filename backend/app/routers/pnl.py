# app/routers/pnl.py
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.pnl.service import PnlService
from app.pnl.domain import PNLPeriod

router = APIRouter(prefix="/api/pnl", tags=["pnl"])
svc = PnlService()


# ─────────────────────────────── Schemas ───────────────────────────────

class SummaryResponse(BaseModel):
    period: PNLPeriod
    total_usd: float
    by_exchange: list[dict]
    by_symbol: list[dict]


class SymbolDetailResponse(BaseModel):
    symbol: str
    exchange: str
    account_id: str
    total_usd: float
    components: dict
    last_events: list[dict]


# ─────────────────────────────── Routes ────────────────────────────────

@router.get("/summary", response_model=SummaryResponse)
def get_pnl_summary(
    period: PNLPeriod = Query("today", description="today | wtd | mtd | custom"),
    tz: Optional[str] = Query(None, description="IANA timezone, e.g. Europe/Istanbul"),
    exchange: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    _from: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    """
    If period=custom, 'from' and 'to' (UTC) are required; otherwise, tz is used to compute window.
    """
    scope = {k: v for k, v in {"exchange": exchange, "account_id": account_id}.items() if v}

    if period == "custom":
        if not _from or not to:
            raise HTTPException(status_code=400, detail="for period=custom provide ?from=...&to=... (UTC)")
        total_usd, by_exchange, by_symbol = svc._PnlService__custom_summary(db, _from, to, scope=scope)
        return SummaryResponse(period=period, total_usd=total_usd, by_exchange=by_exchange, by_symbol=by_symbol)

    s = svc.get_summary(db, period=period, tz=tz, scope=scope or None)
    return SummaryResponse(**asdict(s))


@router.get("/symbol/{symbol}", response_model=SymbolDetailResponse)
def get_pnl_symbol_detail(
    symbol: str,
    exchange: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    period: PNLPeriod = Query("today"),
    tz: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    d = svc.get_symbol_detail(
        db,
        symbol=symbol,
        exchange=exchange,
        account_id=account_id,
        period=period,
        tz=tz,
    )
    payload = asdict(d) if is_dataclass(d) else d  # ← handle dict or dataclass
    return SymbolDetailResponse(**payload)

@router.get("/fees", summary="Get fees summary")
def get_fees_summary(
    period: Literal["today", "wtd", "mtd", "custom"] = Query(
        "today",
        description="today | wtd | mtd | custom",
    ),
    tz: Optional[str] = Query(None, description="IANA timezone, e.g. Europe/Istanbul"),
    exchange: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Get total fees paid for a given period.
    Reads from fills table for accurate fee tracking.
    
    Example:
    - GET /api/pnl/fees?period=today
    - GET /api/pnl/fees?period=today&symbol=ETHUSDT
    """
    scope: Dict[str, str] = {}
    if exchange:
        scope["exchange"] = exchange
    if account_id:
        scope["account_id"] = account_id
    if symbol:
        scope["symbol"] = symbol
    
    svc = PnlService()
    result = svc.get_fees_summary(
        db,
        period=period,
        tz=tz,
        scope=scope if scope else None,
    )
    return result


# ─────────────────────────────── Internal (custom period) ──────────────────────

def __custom_summary_delegate(db: Session, svc: PnlService, start_utc: datetime, end_utc: datetime, scope: dict):
    from app.pnl import repository as repo
    total_usd, by_exchange, by_symbol = repo.aggregate_summary(db, start_utc, end_utc, scope or None)
    return total_usd, by_exchange, by_symbol

setattr(PnlService, "_PnlService__custom_summary", staticmethod(__custom_summary_delegate))
