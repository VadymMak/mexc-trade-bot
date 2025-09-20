from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Numeric,
    Boolean,
    Index,
    Enum as SAEnum,
    func,
    text,
)
from sqlalchemy.orm import declarative_mixin

from app.models.base import Base


# ───────────────────────────── Enums ─────────────────────────────

class PositionSide(str, Enum):
    BUY = "BUY"    # long
    SELL = "SELL"  # short


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


# ─────────────────────── Common mixins/columns ───────────────────

@declarative_mixin
class TimestampsMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


@declarative_mixin
class RevisionMixin:
    # Application-managed optimistic revision. Bump when mutating the row.
    revision = Column(Integer, server_default=text("1"), nullable=False)


# ─────────────────────────── Positions ───────────────────────────

class Position(Base, TimestampsMixin, RevisionMixin):
    """
    Spot position (workspace-scoped).
    - We allow at most one OPEN position per (workspace_id, symbol, side) at a time at the app level.
      (SQLite lacks native partial unique indexes; we enforce this in service code.)
    - PnL math:
        realized_pnl is cumulative realized profit (in quote currency).
        unrealized_pnl is a convenience snapshot (can be recomputed from last_mark_price).
    """
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)

    # Workspace scoping (default single-workspace = 1)
    workspace_id = Column(Integer, nullable=False, server_default=text("1"))

    # Instrument
    symbol = Column(String(24), nullable=False, index=True)  # e.g., "ATHUSDT"
    side = Column(SAEnum(PositionSide), nullable=False)

    # Quantities & pricing
    qty = Column(Numeric(28, 12), nullable=False)                 # current position size
    entry_price = Column(Numeric(28, 12), nullable=False)         # VWAP entry/avg price
    last_mark_price: Optional[Numeric] = Column(Numeric(28, 12))  # last seen mark/quote

    # PnL (quote currency)
    realized_pnl = Column(Numeric(28, 12), nullable=False, server_default=text("0"))
    unrealized_pnl: Optional[Numeric] = Column(Numeric(28, 12))   # snapshot cache (optional)

    # Lifecycle
    status = Column(SAEnum(PositionStatus), nullable=False, server_default=text("'OPEN'"))
    opened_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    closed_at = Column(DateTime(timezone=True))

    # Flags
    is_open = Column(Boolean, nullable=False, server_default=text("1"))

    # Notes / attribution
    note = Column(String(255))

    # Helpful composite indexes for common queries
    __table_args__ = (
        Index("ix_positions_ws_symbol", "workspace_id", "symbol"),
        Index("ix_positions_ws_open", "workspace_id", "is_open"),
        Index("ix_positions_ws_status", "workspace_id", "status"),
        Index("ix_positions_ws_updated", "workspace_id", "updated_at"),
        Index("ix_positions_ws_symbol_side_open", "workspace_id", "symbol", "side", "is_open"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Position id={self.id} ws={self.workspace_id} {self.symbol} "
            f"{self.side} qty={self.qty} @ {self.entry_price} "
            f"status={self.status} open={self.is_open}>"
        )
