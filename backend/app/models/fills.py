from __future__ import annotations
from enum import Enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Numeric,
    Boolean,
    Index,
    ForeignKey,
    Enum as SAEnum,
    func,
    text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_mixin

from app.models.base import Base


# ───────────────────────────── Enums ─────────────────────────────
class Liquidity(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class FillSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


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
    # Application-managed optimistic revision. Bump on mutation.
    revision = Column(Integer, server_default=text("1"), nullable=False)


# ───────────────────────────── Fills ─────────────────────────────
class Fill(Base, TimestampsMixin, RevisionMixin):
    """
    Per-execution record (workspace-scoped).
    """
    __tablename__ = "fills"

    id = Column(Integer, primary_key=True)

    # Workspace scoping (default single-workspace = 1)
    workspace_id = Column(Integer, nullable=False, server_default=text("1"))

    # Optional link to orders table
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)

    # Instrument and side
    symbol = Column(String(24), nullable=False, index=True)  # e.g., "ATHUSDT"
    side = Column(SAEnum(FillSide), nullable=False)

    # Execution economics
    qty = Column(Numeric(28, 12), nullable=False)            # base amount
    price = Column(Numeric(28, 12), nullable=False)          # quote/base
    quote_qty = Column(Numeric(28, 12))                      # optional cache: qty * price

    fee = Column(Numeric(28, 12), nullable=False, server_default=text("0"))
    fee_asset = Column(String(16))                           # e.g., "USDT"
    liquidity = Column(SAEnum(Liquidity))                    # MAKER/TAKER
    is_maker = Column(Boolean, nullable=False, server_default=text("0"))  # quick filter

    # IDs for reconciliation
    client_order_id = Column(String(64), nullable=True)
    exchange_order_id = Column(String(64), nullable=True)
    trade_id = Column(String(64), nullable=True)             # venue trade id if available

    # Timing (nullable so executor can pass None; DB default still applies if omitted)
    executed_at = Column(DateTime(timezone=True), nullable=True, server_default=func.now())

    # Optional attribution / notes
    strategy_tag = Column(String(64))
    note = Column(String(255))

    __table_args__ = (
        # ⚠️ No DEFERRABLE for SQLite compatibility
        UniqueConstraint("workspace_id", "symbol", "trade_id", name="uq_fills_ws_symbol_trade"),
        Index("ix_fills_ws_symbol_time", "workspace_id", "symbol", "executed_at"),
        Index("ix_fills_ws_coid", "workspace_id", "client_order_id"),
        Index("ix_fills_ws_exoid", "workspace_id", "exchange_order_id"),
        Index("ix_fills_ws_updated", "workspace_id", "updated_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Fill id={self.id} ws={self.workspace_id} {self.symbol} {self.side} "
            f"qty={self.qty} @ {self.price} exec_at={self.executed_at} coid={self.client_order_id}>"
        )
