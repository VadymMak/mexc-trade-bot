# app/models/pnl_ledger.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PnlLedger(Base):
    """
    One row per realized-affecting event.

    Notes:
    - SQLite stores DateTime as TEXT under the hood. We keep it as DateTime in ORM
      and write UTC datetimes (naive or tz-normalized to UTC).
    - JSON maps to TEXT on SQLite via SQLAlchemy's JSON type.
    - amount_asset and amount_usd are sign-aware.
    """

    __tablename__ = "pnl_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # UTC timestamp (store UTC). If your app produces aware datetimes, normalize to UTC before commit.
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=False)

    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)

    symbol: Mapped[str] = mapped_column(String(64), nullable=False)       # e.g., "ETHUSDT"
    base_asset: Mapped[str] = mapped_column(String(32), nullable=False)   # e.g., "ETH"
    quote_asset: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g., "USDT"

    # TRADE_REALIZED | FEE | FUNDING | CONVERSION_PNL
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Native asset delta and normalized USDT(eq). Use Decimal-friendly Numeric.
    amount_asset: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)

    # Optional linkage to orders/trades
    ref_order_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ref_trade_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Raw details for audit. On SQLite this is TEXT with JSON1-enabled ops.
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        # Helpful composite scope indexes
        Index("pnl_ledger_ts_idx", "ts"),
        Index("pnl_ledger_scope_idx", "exchange", "account_id", "symbol"),
        Index("pnl_ledger_event_idx", "event_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PnlLedger id={self.id} ts={self.ts} {self.exchange}:{self.account_id} "
            f"{self.symbol} type={self.event_type} usd={self.amount_usd}>"
        )
