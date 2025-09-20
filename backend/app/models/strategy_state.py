# app/models/strategy_state.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import (
    Integer,
    BigInteger,
    DateTime,
    JSON,
    UniqueConstraint,
    Index,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StrategyState(Base):
    """
    Server-side state of strategies per symbol:
    - per_symbol: {"ATHUSDT": {"running": true, "params": {...}}, ...}
    - revision: monotonic version for optimistic concurrency (ETag/If-Match)
    """

    __tablename__ = "strategy_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # For multi-workspace future; default=1
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # JSON field: per-symbol strategy state
    per_symbol: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Revision for optimistic concurrency
    revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_strategy_state_workspace"),
        Index("ix_strategy_state_workspace", "workspace_id"),
        Index("ix_strategy_state_revision", "revision"),
        Index("ix_strategy_state_updated", "updated_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "per_symbol": self.per_symbol or {},
            "revision": int(self.revision) if self.revision is not None else 0,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def bump_revision(self, value: int = 1) -> None:
        """Increment revision (min 1)."""
        cur = int(self.revision or 0)
        inc = int(value or 0)
        self.revision = max(1, cur + inc)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StrategyState ws={self.workspace_id} rev={self.revision} updated_at={self.updated_at}>"
