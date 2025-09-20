# app/execution/router.py
from __future__ import annotations

from typing import Dict, Optional

from app.execution.paper_executor import PaperExecutor
# from app.execution.live_executor import LiveExecutor  # future
from app.config.settings import settings
from app.models.base import SessionLocal


class ExecutionRouter:
    """
    Picks the correct execution port depending on mode.
    - PaperExecutor is instantiated per workspace_id (default=1).
    - Each PaperExecutor is wired with SessionLocal so Orders/Fills/Positions persist.
    """
    def __init__(self) -> None:
        self._paper_by_ws: Dict[int, PaperExecutor] = {}
        # self._live_by_ws: Dict[int, LiveExecutor] = {}

    def get_port(self, workspace_id: int = 1) -> PaperExecutor:  # | LiveExecutor in future
        mode = (getattr(settings, "mode", "paper") or "paper").lower()
        if mode == "live":
            # TODO: wire LiveExecutor here when ready
            # return self._get_live(workspace_id)
            # For now, fall back to paper to avoid crashes.
            return self._get_paper(workspace_id)
        return self._get_paper(workspace_id)

    def _get_paper(self, workspace_id: int) -> PaperExecutor:
        if workspace_id not in self._paper_by_ws:
            # Wire to DB for persistence
            self._paper_by_ws[workspace_id] = PaperExecutor(
                session_factory=SessionLocal,
                workspace_id=workspace_id,
            )
        return self._paper_by_ws[workspace_id]

    # def _get_live(self, workspace_id: int) -> LiveExecutor:
    #     if workspace_id not in self._live_by_ws:
    #         self._live_by_ws[workspace_id] = LiveExecutor(...)
    #     return self._live_by_ws[workspace_id]


# Global router instance
exec_router = ExecutionRouter()
