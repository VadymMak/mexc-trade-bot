# app/models/ui_state.py
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
    Boolean,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UIState(Base):
    """
    Server-side UI state for a workspace.
    - watchlist/layout/ui_prefs are JSON blobs (SQLite & Postgres friendly)
    - revision used as ETag/If-Match; increment via bump_revision()
    """

    __tablename__ = "ui_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Keep workspace_id for easy multi-tenant later
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # JSON fields
    watchlist: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    layout: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ui_prefs: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Monotonic revision for optimistic concurrency / ETag
    revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )

    # Active flag (added to fix 'no attribute active' error)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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
        UniqueConstraint("workspace_id", name="uq_ui_state_workspace"),
        Index("ix_ui_state_workspace", "workspace_id"),
        Index("ix_ui_state_revision", "revision"),
        Index("ix_ui_state_updated", "updated_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "watchlist": self.watchlist or {},
            "layout": self.layout or {},
            "ui_prefs": self.ui_prefs or {},
            "revision": int(self.revision) if self.revision is not None else 0,
            "active": self.active,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def bump_revision(self, value: int = 1) -> None:
        cur = int(self.revision or 0)
        inc = int(value or 0)
        self.revision = max(1, cur + inc)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UIState ws={self.workspace_id} rev={self.revision} active={self.active} updated_at={self.updated_at}>"