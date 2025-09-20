from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
    Index,
    func,
    text,
)
from app.models.base import Base


class Session(Base):
    """
    Logical session grouping for UI and strategy state.
    - For now mostly a placeholder; can be expanded with user_id, role_id, etc.
    - SessionManager service can ensure one active session per workspace.
    """
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)

    # Workspace scoping
    workspace_id = Column(Integer, nullable=False, server_default=text("1"))

    # Labels & metadata
    name = Column(String(64), nullable=True)
    description = Column(String(255), nullable=True)

    # Lifecycle
    is_active = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_sessions_ws_active", "workspace_id", "is_active"),
        Index("ix_sessions_ws_updated", "workspace_id", "updated_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Session id={self.id} ws={self.workspace_id} active={self.is_active}>"
