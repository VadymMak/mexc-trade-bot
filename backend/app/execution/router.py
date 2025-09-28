# app/execution/router.py
from __future__ import annotations

from typing import Dict

from app.config.settings import settings
from app.models.base import SessionLocal
from app.execution.paper_executor import PaperExecutor, PositionTrackerProto
from app.services.position_tracker import PositionTracker  # your concrete tracker


class ExecutionRouter:
    def __init__(self) -> None:
        self._paper_by_ws: Dict[int, PaperExecutor] = {}
        self._tracker_by_ws: Dict[int, PositionTrackerProto] = {}

    def get_tracker(self, workspace_id: int = 1) -> PositionTrackerProto:
        tr = self._tracker_by_ws.get(workspace_id)
        if tr is None:
            # NOTE: PositionTracker currently requires a live DB session
            db = SessionLocal()  # long-lived session owned by the tracker
            tr = PositionTracker(db=db, workspace_id=workspace_id)
            self._tracker_by_ws[workspace_id] = tr
        return tr

    def get_port(self, workspace_id: int = 1) -> PaperExecutor:
        if settings.is_paper or settings.is_demo or settings.is_live:
            return self._get_paper(workspace_id)
        return self._get_paper(workspace_id)

    def _get_paper(self, workspace_id: int) -> PaperExecutor:
        port = self._paper_by_ws.get(workspace_id)
        if port is None:
            tracker = self.get_tracker(workspace_id)
            port = PaperExecutor(
                session_factory=SessionLocal,
                workspace_id=workspace_id,
                position_tracker=tracker,
            )
            self._paper_by_ws[workspace_id] = port
        return port

    def _get_paper(self, workspace_id: int) -> PaperExecutor:
        port = self._paper_by_ws.get(workspace_id)
        if port is None:
            tracker = self.get_tracker(workspace_id)
            port = PaperExecutor(
                session_factory=SessionLocal,
                workspace_id=workspace_id,
                position_tracker=tracker,  # inject durable tracker
            )
            self._paper_by_ws[workspace_id] = port
        return port

    # def _get_live(self, workspace_id: int) -> LiveExecutor:
    #     if workspace_id not in self._live_by_ws:
    #         tracker = self.get_tracker(workspace_id)
    #         self._live_by_ws[workspace_id] = LiveExecutor(..., position_tracker=tracker)
    #     return self._live_by_ws[workspace_id]


# Global router instance
exec_router = ExecutionRouter()
