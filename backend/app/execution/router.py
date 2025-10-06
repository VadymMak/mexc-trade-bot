# app/execution/router.py
from __future__ import annotations

import asyncio
from typing import Dict, Optional, Protocol, Any

from app.config.settings import settings
from app.models.base import SessionLocal
from app.execution.paper_executor import PaperExecutor, PositionTrackerProto
from app.services.position_tracker import PositionTracker  # concrete tracker

# Live executor is optional; import guarded so PAPER still works without it
try:
    from app.execution.live_executor import LiveExecutor  # type: ignore
    _LIVE_OK = True
except Exception:
    LiveExecutor = None  # type: ignore
    _LIVE_OK = False


# Protocol common to both executors (what StrategyEngine expects)
class ExecutionPort(Protocol):
    async def start_symbol(self, symbol: str) -> None: ...
    async def stop_symbol(self, symbol: str) -> None: ...
    async def flatten_symbol(self, symbol: str) -> None: ...
    async def cancel_orders(self, symbol: str) -> None: ...
    async def place_maker(self, symbol: str, side: str, price: float, qty: float, tag: str = "mm") -> Optional[str]: ...
    async def get_position(self, symbol: str) -> dict: ...


class ExecutionRouter:
    """
    Per-workspace singletons for executors and the durable PositionTracker.
    Chooses Paper vs Live based on `settings` flags.
    """
    def __init__(self) -> None:
        self._paper_by_ws: Dict[int, PaperExecutor] = {}
        self._tracker_by_ws: Dict[int, PositionTrackerProto] = {}
        self._live_by_ws: Dict[int, Any] = {}  # LiveExecutor instances when available

    # ───── trackers ─────
    def get_tracker(self, workspace_id: int = 1) -> PositionTrackerProto:
        tracker = self._tracker_by_ws.get(workspace_id)
        if tracker is None:
            # PositionTracker owns its DB session; lifetime = workspace singleton
            db_session = SessionLocal()
            tracker = PositionTracker(db=db_session, workspace_id=workspace_id)
            self._tracker_by_ws[workspace_id] = tracker
        return tracker

    # ───── ports ─────
    def get_port(self, workspace_id: int = 1) -> ExecutionPort:
        """
        Choose an execution port based on current mode:
        - PAPER / DEMO → PaperExecutor
        - LIVE        → LiveExecutor (if available), else fallback to Paper
        """
        if settings.is_paper or settings.is_demo:
            return self._get_paper(workspace_id)

        if settings.is_live:
            if _LIVE_OK:
                try:
                    return self._get_live(workspace_id)
                except Exception:
                    # hard-fallback to paper if live wiring fails
                    return self._get_paper(workspace_id)
            # no live module available → fallback
            return self._get_paper(workspace_id)

        # default fallback
        return self._get_paper(workspace_id)

    def _get_paper(self, workspace_id: int) -> PaperExecutor:
        port = self._paper_by_ws.get(workspace_id)
        if port is None:
            tracker = self.get_tracker(workspace_id)
            port = PaperExecutor(
                session_factory=SessionLocal,
                workspace_id=workspace_id,
                position_tracker=tracker,  # durable tracker
            )
            self._paper_by_ws[workspace_id] = port
        return port

    def _get_live(self, workspace_id: int) -> ExecutionPort:
        port = self._live_by_ws.get(workspace_id)
        if port is None:
            # LiveExecutor currently only needs the session factory (for PnL ledger)
            port = LiveExecutor(  # type: ignore[operator]
                session_factory=SessionLocal,
                workspace_id=workspace_id,
            )
            self._live_by_ws[workspace_id] = port
        return port  # type: ignore[return-value]

    # ───── lifecycle / reset ─────
    def reset(self, workspace_id: Optional[int] = None) -> None:
        """
        Safely reset ports and trackers. Closes tracker resources if present and
        acloses live executors' network clients when possible. Clears caches for
        a specific workspace or all.
        """

        def _safe_close_tracker(t: PositionTrackerProto) -> None:
            # optional close() on tracker
            if hasattr(t, "close") and callable(getattr(t, "close")):
                try:
                    t.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
            db = getattr(t, "db", None)
            if db and hasattr(db, "close"):
                try:
                    db.close()
                except Exception:
                    pass

        def _safe_aclose_live(port: Any) -> None:
            """Best-effort async close for live executor (if it exposes aclose())."""
            aclose = getattr(port, "aclose", None)
            if not callable(aclose):
                return
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # fire-and-forget task on the running loop
                    loop.create_task(aclose())  # type: ignore[misc]
                else:
                    loop.run_until_complete(aclose())  # type: ignore[misc]
            except RuntimeError:
                # No loop; create a temporary one
                try:
                    asyncio.run(aclose())  # type: ignore[misc]
                except Exception:
                    pass
            except Exception:
                pass

        if workspace_id is not None:
            # close tracker for a single workspace
            tracker = self._tracker_by_ws.pop(workspace_id, None)
            if tracker is not None:
                _safe_close_tracker(tracker)
            # drop paper executor
            self._paper_by_ws.pop(workspace_id, None)
            # aclose live executor if present
            live = self._live_by_ws.pop(workspace_id, None)
            if live is not None:
                _safe_aclose_live(live)
            return

        # otherwise reset all
        for t in list(self._tracker_by_ws.values()):
            _safe_close_tracker(t)
        self._tracker_by_ws.clear()

        self._paper_by_ws.clear()

        for live in list(self._live_by_ws.values()):
            _safe_aclose_live(live)
        self._live_by_ws.clear()


# Global router instance
exec_router = ExecutionRouter()
