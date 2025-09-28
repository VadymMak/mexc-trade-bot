# app/models/pnl_daily.py
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PnlDaily(Base):
    """
    End-of-day pre-aggregated realized PnL totals (UTC date scope).

    Uniqueness:
      (date, exchange, account_id, symbol) is unique.

    Notes (SQLite-friendly):
    - Date maps cleanly to TEXT (YYYY-MM-DD) in SQLite.
    - updated_at uses CURRENT_TIMESTAMP via SQLAlchemy func for portability.
    - realized_usd/fees_usd are sign-aware NUMERIC(38,18).
    """

    __tablename__ = "pnl_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # UTC day bucket
    date: Mapped[date] = mapped_column(Date, nullable=False, index=False)

    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)

    realized_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    fees_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)

    # Last time this row was updated (UTC). On SQLite this is TEXT with CURRENT_TIMESTAMP.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        UniqueConstraint("date", "exchange", "account_id", "symbol", name="pnl_daily_unique_idx"),
        Index("pnl_daily_date_idx", "date"),
        Index("pnl_daily_scope_idx", "exchange", "account_id", "symbol"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PnlDaily {self.date} {self.exchange}:{self.account_id} {self.symbol} "
            f"realized={self.realized_usd} fees={self.fees_usd}>"
        )
