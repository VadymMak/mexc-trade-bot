# app/models/orders.py
from __future__ import annotations
from enum import Enum
from sqlalchemy import (
    Column, Integer, String, DateTime, Numeric, Boolean, Index,
    UniqueConstraint, Enum as SAEnum, func, text
)
from sqlalchemy.orm import declarative_mixin
from app.models.base import Base

# ─────────────── Enums ───────────────
class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"

class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

# ─────────────── Mixins ───────────────
@declarative_mixin
class TimestampsMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

@declarative_mixin
class RevisionMixin:
    revision = Column(Integer, server_default=text("1"), nullable=False)

# ─────────────── Orders ───────────────
class Order(Base, TimestampsMixin, RevisionMixin):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, nullable=False, server_default=text("1"))

    symbol = Column(String(24), nullable=False, index=True)
    side = Column(SAEnum(OrderSide), nullable=False)
    type = Column(SAEnum(OrderType), nullable=False, server_default="LIMIT")
    tif = Column(SAEnum(TimeInForce), nullable=False, server_default="GTC")

    qty = Column(Numeric(28, 12), nullable=False)
    price = Column(Numeric(28, 12))
    filled_qty = Column(Numeric(28, 12), nullable=False, server_default=text("0"))
    avg_fill_price = Column(Numeric(28, 12))

    status = Column(SAEnum(OrderStatus), nullable=False, server_default="NEW")
    is_active = Column(Boolean, nullable=False, server_default=text("1"))
    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_event_at = Column(DateTime(timezone=True))
    canceled_at = Column(DateTime(timezone=True))

    strategy_tag = Column(String(64))
    reduce_only = Column(Boolean, nullable=False, server_default=text("0"))
    post_only = Column(Boolean, nullable=False, server_default=text("0"))

    client_order_id = Column(String(64), nullable=False)
    exchange_order_id = Column(String(64))
    note = Column(String(255))

    __table_args__ = (
        UniqueConstraint("workspace_id", "client_order_id", name="uq_orders_ws_client_id"),
        Index("ix_orders_ws_symbol", "workspace_id", "symbol"),
        Index("ix_orders_ws_status", "workspace_id", "status"),
        Index("ix_orders_ws_active", "workspace_id", "is_active"),
        Index("ix_orders_ws_updated", "workspace_id", "updated_at"),
        Index("ix_orders_ws_exchange_id", "workspace_id", "exchange_order_id"),
    )

    @property
    def remaining_qty(self):
        try:
            return (self.qty or 0) - (self.filled_qty or 0)
        except Exception:
            return None

    def __repr__(self) -> str:
        return (
            f"<Order id={self.id} ws={self.workspace_id} {self.symbol} {self.side} "
            f"{self.type}@{self.price} qty={self.qty} filled={self.filled_qty} "
            f"status={self.status} active={self.is_active} coid={self.client_order_id}>"
        )
