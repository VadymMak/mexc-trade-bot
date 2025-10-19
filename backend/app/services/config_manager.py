# app/services/config_manager.py
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.config.settings import settings

__all__ = ["ConfigManager", "ConfigState", "config_manager"]

logger = logging.getLogger(__name__)

Provider = str  # "gate" | "mexc" | "binance"
Mode = str      # "PAPER" | "DEMO" | "LIVE"


@dataclass
class ConfigState:
    active: Provider         # lower-case provider
    mode: Mode               # UPPER mode
    available: List[Provider]
    ws_enabled: bool
    revision: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConfigManager:
    """
    Orchestrates provider/mode state and safe switching with idempotency.
    Wire hooks from main.py via `set_hooks(...)`.
    """

    # Pull the TTL from settings to keep one source of truth
    _IDEMPOTENCY_TTL = int(getattr(settings, "idempotency_window_sec", 300))

    def __init__(
        self,
        initial_provider: Provider = settings.active_provider.lower(),
        initial_mode: Mode = settings.active_mode.upper(),
        available_providers: Optional[List[Provider]] = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

        # Keep provider list normalized to lowercase and in a stable UI-friendly order
        self._available = [p.lower() for p in (available_providers or settings.available_providers)]
        self._state = ConfigState(
            active=initial_provider,
            mode=initial_mode,
            available=self._available,
            ws_enabled=bool(settings.enable_ws),
            revision=1,  # will be replaced from DB if present
        )

        # Hooks (wired in main.py)
        self._hook_stop_all_strategies: Optional[Callable[[], Awaitable[None]]] = None
        self._hook_stop_streams: Optional[Callable[[], Awaitable[None]]] = None
        self._hook_start_streams: Optional[Callable[[Provider, Mode], Awaitable[bool]]] = None
        self._hook_reset_book_tracker: Optional[Callable[[], None]] = None

        # Idempotency cache: key -> (timestamp, state_dict)
        self._idem_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

        # Clients (reload on switch)
        self._private_client: Optional[Any] = None
        self._public_client: Optional[Any] = None

    # ─────────────────────────── Wiring / Hooks ────────────────────────────
    def set_hooks(
        self,
        *,
        stop_all_strategies: Callable[[], Awaitable[None]],
        stop_streams: Callable[[], Awaitable[None]],
        start_streams: Callable[[Provider, Mode], Awaitable[bool]],
        reset_book_tracker: Callable[[], None],
    ) -> None:
        self._hook_stop_all_strategies = stop_all_strategies
        self._hook_stop_streams = stop_streams
        self._hook_start_streams = start_streams
        self._hook_reset_book_tracker = reset_book_tracker

    # ─────────────────────────── Initialization ────────────────────────────
    async def init_on_startup(self, db: Any = None) -> None:
        """
        Called once from app lifespan. Starts streams for the initial provider/mode.
        Loads persisted state from DB if provided.
        """
        if not all(
            [
                self._hook_stop_all_strategies,
                self._hook_stop_streams,
                self._hook_start_streams,
                self._hook_reset_book_tracker,
            ]
        ):
            raise RuntimeError("ConfigManager hooks are not fully wired. Call set_hooks(...) before init_on_startup().")

        # Load persisted state from DB (if any)
        if db:
            try:
                persisted = self._load_from_db(db)
                # Normalize and overlay
                persisted["active"] = str(persisted.get("active", self._state.active)).lower()
                persisted["mode"] = str(persisted.get("mode", self._state.mode)).upper()
                persisted["ws_enabled"] = bool(persisted.get("ws_enabled", self._state.ws_enabled))
                persisted["revision"] = int(persisted.get("revision", self._state.revision))
                persisted["available"] = list(self._available)
                self._state = ConfigState(**persisted)
                logger.info("ConfigManager: loaded persisted state from DB: %s/%s", self._state.active, self._state.mode)
            except Exception as e:
                logger.info("ConfigManager: no persisted state found (using initial). %s", e)

        async with self._lock:
            logger.info("ConfigManager: initializing — provider=%s, mode=%s", self._state.active, self._state.mode)
            try:
                self._hook_reset_book_tracker and self._hook_reset_book_tracker()
            except Exception:
                logger.exception("ConfigManager: reset_book_tracker on startup failed (continuing).")

            self._reload_clients(self._state.active, self._state.mode)

            ws_enabled = await self._hook_start_streams(self._state.active, self._state.mode)  # type: ignore[arg-type]
            self._state.ws_enabled = bool(ws_enabled)
            self._ready.set()
            logger.info("ConfigManager: initialized — ws_enabled=%s, revision=%s", self._state.ws_enabled, self._state.revision)

            if db:
                self._save_to_db(db)

    # ───────────────────────────── Public API ──────────────────────────────
    def is_ready(self) -> bool:
        return self._ready.is_set()

    async def wait_ready(self, timeout: Optional[float] = None) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def get_state(self) -> Dict[str, Any]:
        return dict(self._state.to_dict())

    def state_for_api(self) -> Dict[str, Any]:
        """
        Shape aligned with settings.provider_state() so the /api/config/provider router
        can return this directly (it may inject 'revision' if needed).
        """
        return {
            "active": self._state.active,
            "mode": self._state.mode,
            "available": list(self._state.available),
            "ws_enabled": bool(self._state.ws_enabled),
            "revision": int(self._state.revision),
        }

    async def switch(
        self,
        *,
        provider: Provider,
        mode: Mode,
        idempotency_key: Optional[str] = None,
        db: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Safely switch provider/mode with idempotency and persistence.
        """
        # Idempotency (fast path)
        if idempotency_key:
            cached = self._idem_cache.get(idempotency_key)
            if cached and (time.time() - cached[0] <= self._IDEMPOTENCY_TTL):
                logger.info("ConfigManager: idempotent switch hit (%s) — returning cached result.", idempotency_key)
                return dict(cached[1])

        provider = (provider or "").strip().lower()
        mode = (mode or "").strip().upper()

        if provider not in self._available:
            raise ValueError(f"Unsupported provider: {provider!r}. Allowed: {self._available}")
        if mode not in {"PAPER", "DEMO", "LIVE"}:
            raise ValueError(f"Unsupported mode: {mode!r}. Allowed: PAPER | DEMO | LIVE")

        async with self._lock:
            same = (provider == self._state.active.lower()) and (mode == self._state.mode.upper())
            if same:
                logger.info("ConfigManager: switch requested to same state (%s/%s) — no-op.", provider, mode)
                state = self._state.to_dict()
                if idempotency_key:
                    self._idem_cache[idempotency_key] = (time.time(), state)
                if db:
                    self._save_to_db(db)
                return dict(state)

            logger.info("ConfigManager: switching provider — %s/%s → %s/%s",
                        self._state.active, self._state.mode, provider, mode)
            self._ready.clear()

            # 1) Stop strategies
            try:
                if self._hook_stop_all_strategies:
                    await self._hook_stop_all_strategies()
            except Exception:
                logger.exception("ConfigManager: stop_all_strategies failed (continuing).")

            # 2) Stop streams
            try:
                if self._hook_stop_streams:
                    await self._hook_stop_streams()
            except Exception:
                logger.exception("ConfigManager: stop_streams failed (continuing).")

            # 3) Reset book tracker
            try:
                if self._hook_reset_book_tracker:
                    self._hook_reset_book_tracker()
            except Exception:
                logger.exception("ConfigManager: reset_book_tracker failed (continuing).")

            # Update target state
            self._state.active = provider
            self._state.mode = mode

            # Reload clients
            self._reload_clients(provider, mode)

            # 4) Start streams
            ws_enabled = False
            try:
                if self._hook_start_streams:
                    ws_enabled = await self._hook_start_streams(provider, mode)
            except Exception:
                logger.exception("ConfigManager: start_streams failed — WS disabled.")
                ws_enabled = False

            # 5) Finalize
            self._state.ws_enabled = bool(ws_enabled)
            self._state.revision += 1
            self._ready.set()

            state = self._state.to_dict()
            logger.info("ConfigManager: switched — %s", state)

            if idempotency_key:
                self._idem_cache[idempotency_key] = (time.time(), state)
            self._prune_idem_cache()

            if db:
                self._save_to_db(db)

            return dict(state)

    # ───────────────────────────── Internals ───────────────────────────────
    def _prune_idem_cache(self) -> None:
        now = time.time()
        stale_keys = [k for k, (ts, _) in self._idem_cache.items() if now - ts > self._IDEMPOTENCY_TTL]
        for k in stale_keys:
            self._idem_cache.pop(k, None)

    def _reload_clients(self, provider: Provider, mode: Mode) -> None:
        try:
            # --- PRIVATE CLIENT: only for LIVE ---
            if mode == "LIVE":
                try:
                    from app.services.exchange_private import get_private_client  # lazy import
                    self._private_client = get_private_client(
                        provider,
                        sandbox=False,   # LIVE → real
                        mock=False,
                    )
                except ValueError as e:
                    # Typical: missing API keys
                    logger.warning("ConfigManager: LIVE private client init failed: %s", e)
                    self._private_client = None
                except Exception as e:
                    logger.exception("ConfigManager: unexpected error creating LIVE private client: %s", e)
                    self._private_client = None
            else:
                # PAPER / DEMO: don't require keys; use no private client by default
                self._private_client = None
                logger.info("ConfigManager: skipping private client init in %s mode", mode)

            # --- PUBLIC CLIENT: optional helper for REST market data (best-effort) ---
            self._public_client = None
            try:
                if provider == "mexc":
                    from app.market_data.mexc_http import MexcHttp
                    self._public_client = MexcHttp(base_url="https://api.mexc.com")
                elif provider == "gate":
                    from app.market_data.http_client import GateHttpClient
                    sandbox_url = "https://api.gateio.ws"  # ✅ Always use production (faster)
                    # OLD: sandbox_url = "https://api.gateio.ws" if (mode == "DEMO") else "https://api.gateio.ws"
                    self._public_client = GateHttpClient(base_url=sandbox_url)
                elif provider == "binance":
                    from app.market_data.binance_http_stub import BinanceHttpStub
                    sandbox_url = "https://testnet.binance.vision" if (mode == "DEMO") else "https://api.binance.com"
                    self._public_client = BinanceHttpStub(base_url=sandbox_url)
                logger.debug("ConfigManager: loaded public client for %s", provider)
            except ImportError as e:
                logger.debug("ConfigManager: public client not available (%s) — continuing without.", e)
                self._public_client = None
            except Exception as e:
                logger.error("ConfigManager: failed to init public client: %s", e)
                self._public_client = None

            logger.info("ConfigManager: reloaded clients for %s/%s", provider, mode)
        except Exception as e:
            logger.exception("ConfigManager: client reload failed (unexpected): %s", e)
            self._private_client = None
            self._public_client = None

    def _load_from_db(self, db: Any) -> Dict[str, Any]:
        """Load persisted state from DB (ui_state). Returns a dict compatible with ConfigState."""
        from app.models.ui_state import UIState  # local import to avoid circulars
        ui = db.query(UIState).first()
        if not ui:
            raise RuntimeError("no ui_state row yet")
        prefs = ui.ui_prefs or {}
        if not isinstance(prefs, dict):
            prefs = {}
        active = str(prefs.get("provider", getattr(ui, "active", settings.active_provider.lower()))).lower()
        mode = str(prefs.get("mode", getattr(ui, "mode", settings.active_mode.upper()))).upper()
        ws_enabled = bool(prefs.get("ws_enabled", settings.enable_ws))
        revision = int(getattr(ui, "revision", 1) or 1)
        return {
            "active": active,
            "mode": mode,
            "ws_enabled": ws_enabled,
            "revision": revision,
            "available": list(self._available),
        }

    def _save_to_db(self, db: Any) -> None:
        """Save state to DB (ui_state)."""
        try:
            from app.models.ui_state import UIState
            ui = db.query(UIState).first()
            if not ui:
                ui = UIState()  # avoid passing unexpected kwargs
                db.add(ui)

            prefs = ui.ui_prefs or {}
            if not isinstance(prefs, dict):
                prefs = {}

            prefs.update({
                "provider": self._state.active,
                "mode": self._state.mode,
                "ws_enabled": self._state.ws_enabled,
            })

            ui.ui_prefs = prefs
            # Persist revision as-is (do not auto-bump here)
            if hasattr(ui, "revision"):
                ui.revision = int(self._state.revision)

            db.commit()
            logger.debug("ConfigManager: saved state to DB (%s/%s, rev=%s)",
                         self._state.active, self._state.mode, self._state.revision)
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            logger.error("ConfigManager: failed to save to DB: %s", e)


# Module-level singleton
config_manager = ConfigManager()
