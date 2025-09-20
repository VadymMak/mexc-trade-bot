# app/services/session_manager.py
from __future__ import annotations

from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import NoResultFound

from app.models.ui_state import UIState
from app.models.strategy_state import StrategyState
from app.config.settings import settings


class SessionManager:
    """
    Manages server-side UI and Strategy state for a workspace.

    - ensure_*: guarantees the row exists in DB
    - open_new_session: clears state and bumps revision
    - get_snapshot: returns a compact dict (UI + Strategy)
    """

    def __init__(self, db: Session, workspace_id: Optional[int] = None) -> None:
        self.db: Session = db
        self.workspace_id: int = int(workspace_id or settings.workspace_id)

    # ---------------- Internals ----------------
    @staticmethod
    def _seed_revision() -> int:
        """Initial revision seed (configurable)."""
        return int(getattr(settings, "ui_revision_seed", 1))

    # ---------------- UI State ----------------
    def ensure_ui_state(self) -> UIState:
        try:
            state: UIState = (
                self.db.query(UIState)
                .filter(UIState.workspace_id == self.workspace_id)
                .one()
            )
            return state
        except NoResultFound:
            state = UIState(
                workspace_id=self.workspace_id,
                watchlist={},
                layout={},
                ui_prefs={},
                revision=self._seed_revision(),
            )
            self.db.add(state)
            self.db.commit()
            # refresh to pick up timestamps/defaults
            self.db.refresh(state)
            return state

    # ---------------- Strategy State ----------------
    def ensure_strategy_state(self) -> StrategyState:
        try:
            state: StrategyState = (
                self.db.query(StrategyState)
                .filter(StrategyState.workspace_id == self.workspace_id)
                .one()
            )
            return state
        except NoResultFound:
            state = StrategyState(
                workspace_id=self.workspace_id,
                per_symbol={},
                revision=1,
            )
            self.db.add(state)
            self.db.commit()
            self.db.refresh(state)
            return state

    # ---------------- Session management ----------------
    def open_new_session(self) -> Dict[str, Any]:
        """
        Clears UI + Strategy for the workspace and returns a fresh snapshot.
        """
        ui = self.ensure_ui_state()
        strat = self.ensure_strategy_state()

        # reset UI
        ui.watchlist = {}
        ui.layout = {}
        ui.ui_prefs = {}
        ui.bump_revision()

        # reset Strategy
        strat.per_symbol = {}
        strat.bump_revision()

        self.db.add(ui)
        self.db.add(strat)
        self.db.commit()

        return self.get_snapshot()

    # ---------------- Snapshot ----------------
    def get_snapshot(self) -> Dict[str, Any]:
        """
        Build the current snapshot for the frontend.
        (Orders/Fills/Positions are added in the router via ?include=...)
        """
        ui = self.ensure_ui_state()
        strat = self.ensure_strategy_state()
        return {
            "ui_state": ui.to_dict(),
            "strategy_state": strat.to_dict(),
        }
