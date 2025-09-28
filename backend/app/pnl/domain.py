# app/pnl/domain.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple, TYPE_CHECKING, Union

# ── Type-only import to keep Pylance happy ──────────────────────────────────────
if TYPE_CHECKING:
    from zoneinfo import ZoneInfo  # only for type checking

# Runtime import (may be unavailable in some environments)
try:
    from zoneinfo import ZoneInfo as RuntimeZoneInfo  # for actual runtime use
except Exception:  # pragma: no cover
    RuntimeZoneInfo = None  # type: ignore


# ─────────────────────────────── Enums & literals ───────────────────────────────

class PNLEventType(str, Enum):
    TRADE_REALIZED = "TRADE_REALIZED"
    FEE = "FEE"
    FUNDING = "FUNDING"
    CONVERSION_PNL = "CONVERSION_PNL"


PNLPeriod = Literal["today", "wtd", "mtd", "custom"]


# ─────────────────────────────── Domain dataclasses ─────────────────────────────

@dataclass
class PNLLedgerEvent:
    ts: datetime
    exchange: str
    account_id: str
    symbol: str
    base_asset: str
    quote_asset: str
    event_type: PNLEventType
    amount_asset: str  # Decimal-as-string at the service edge
    amount_usd: str    # Decimal-as-string at the service edge
    ref_order_id: Optional[str] = None
    ref_trade_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PnLComponents:
    trade_realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    conversion: float = 0.0


@dataclass
class PnlSymbolDetail:
    symbol: str
    exchange: str
    account_id: str
    total_usd: float
    components: PnLComponents
    last_events: List[Dict[str, Any]]


@dataclass
class PnlSummary:
    period: PNLPeriod
    total_usd: float
    by_exchange: List[Dict[str, Any]]
    by_symbol: List[Dict[str, Any]]


@dataclass
class PortfolioAsset:
    asset: str
    free: float
    locked: float
    usd_eq: float
    exchange: str
    account_id: str


# ─────────────────────────────── Time window helpers ────────────────────────────

def _tz_or_utc(tz: Optional[str]) -> Union[timezone, "ZoneInfo"]:
    """
    Returns ZoneInfo(tz) if available, else UTC.
    Accepts None → UTC.
    """
    if tz and RuntimeZoneInfo is not None:
        try:
            return RuntimeZoneInfo(tz)  # type: ignore[misc,call-arg]
        except Exception:
            pass
    return timezone.utc


def period_window(
    period: PNLPeriod,
    tz: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """
    Compute [start, end) UTC window for the given period using the provided timezone.

    - today: local midnight → next midnight
    - wtd:   Monday 00:00 local → end of today
    - mtd:   1st day 00:00 local → end of today
    - custom: caller must provide explicit from/to upstream
    """
    if period == "custom":
        raise ValueError("period_window('custom') requires explicit from/to; compute upstream")

    tzinfo = _tz_or_utc(tz)
    _now = now or datetime.now(tzinfo)

    local_now = _now.astimezone(tzinfo)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "today":
        start_local = local_midnight
        end_local = local_midnight + timedelta(days=1)  # exclusive
    elif period == "wtd":
        start_local = local_midnight - timedelta(days=local_now.weekday())  # Monday
        # end inclusive → +1 microsecond then normalize to exclusive
        end_local = local_now.replace(hour=23, minute=59, second=59, microsecond=999999) + timedelta(microseconds=1)
    elif period == "mtd":
        start_local = local_midnight.replace(day=1)
        end_local = local_now.replace(hour=23, minute=59, second=59, microsecond=999999) + timedelta(microseconds=1)
    else:
        raise ValueError(f"Unsupported period: {period}")

    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    return start_utc, end_utc


def ensure_utc(dt: datetime) -> datetime:
    """Normalize datetime to UTC (aware). Treat naive as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
