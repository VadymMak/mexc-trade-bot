# app/services/config_manager.py
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

__all__ = ["ConfigManager", "ConfigState", "config_manager"]

# ──────────────────────────────────────────────────────────────────────────────
# Public contract
# - ConfigManager is a singleton-like orchestrator that:
#   * stores active provider/mode
#   * coordinates safe provider switches (stop strategies → stop streams → reset tracker → start streams)
#   * exposes GET/POST-friendly state (for /api/config/provider)
# - It does NOT hard-depend on specific services; instead, it accepts hooks that
#   will be wired from main.py:
#     - stop_all_strategies()
#     - stop_streams()
#     - start_streams(provider, mode) -> bool (ws_enabled)
#     - reset_book_tracker()
#   You can wire existing services/session managers without refactoring them.
# ──────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

Provider = str  # e.g. "gate" | "mexc" | "binance"
Mode = str      # e.g. "PAPER" | "DEMO" | "LIVE"


@dataclass
class ConfigState:
    active: Provider
    mode: Mode
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

    # How long we remember idempotency decisions (seconds)
    _IDEMPOTENCY_TTL = 5 * 60

    def __init__(
        self,
        initial_provider: Provider = "gate",
        initial_mode: Mode = "PAPER",
        available_providers: Optional[List[Provider]] = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

        self._available = available_providers or ["gate", "mexc", "binance"]
        self._state = ConfigState(
            active=initial_provider,
            mode=initial_mode,
            available=self._available,
            ws_enabled=False,
            revision=1,
        )

        # Hooks (to be wired in main.py)
        self._hook_stop_all_strategies: Optional[Callable[[], Awaitable[None]]] = None
        self._hook_stop_streams: Optional[Callable[[], Awaitable[None]]] = None
        self._hook_start_streams: Optional[Callable[[Provider, Mode], Awaitable[bool]]] = None
        self._hook_reset_book_tracker: Optional[Callable[[], None]] = None

        # Idempotency memory: key -> (timestamp, state_dict)
        self._idem_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    # ─────────────────────────── Wiring / Hooks ────────────────────────────

    def set_hooks(
        self,
        *,
        stop_all_strategies: Callable[[], Awaitable[None]],
        stop_streams: Callable[[], Awaitable[None]],
        start_streams: Callable[[Provider, Mode], Awaitable[bool]],
        reset_book_tracker: Callable[[], None],
    ) -> None:
        """Wire lifecycle hooks from the app."""
        self._hook_stop_all_strategies = stop_all_strategies
        self._hook_stop_streams = stop_streams
        self._hook_start_streams = start_streams
        self._hook_reset_book_tracker = reset_book_tracker

    # ─────────────────────────── Initialization ────────────────────────────

    async def init_on_startup(self) -> None:
        """
        Called once from app lifespan. Starts streams for the initial provider/mode.
        """
        if not all(
            [
                self._hook_stop_all_strategies,
                self._hook_stop_streams,
                self._hook_start_streams,
                self._hook_reset_book_tracker,
            ]
        ):
            raise RuntimeError(
                "ConfigManager hooks are not fully wired. Call set_hooks(...) before init_on_startup()."
            )

        async with self._lock:
            logger.info(
                "ConfigManager: initializing — provider=%s, mode=%s",
                self._state.active,
                self._state.mode,
            )
            # Ensure tracker is clean on boot (defensive)
            try:
                self._hook_reset_book_tracker and self._hook_reset_book_tracker()
            except Exception:
                logger.exception("ConfigManager: reset_book_tracker on startup failed (continuing).")

            # Start streams for initial provider/mode
            ws_enabled = await self._hook_start_streams(self._state.active, self._state.mode)
            self._state.ws_enabled = bool(ws_enabled)
            self._ready.set()
            logger.info(
                "ConfigManager: initialized — ws_enabled=%s, revision=%s",
                self._state.ws_enabled,
                self._state.revision,
            )

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
        """
        Return current config state (for GET /api/config/provider).
        Fast, non-blocking; returns a shallow copy to prevent accidental external mutation.
        """
        d = self._state.to_dict()
        return dict(d)  # shallow copy

    async def switch(
        self,
        *,
        provider: Provider,
        mode: Mode,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Safely switch provider/mode:
          1) stop strategies
          2) stop streams
          3) reset book tracker
          4) start streams for new provider/mode
          5) bump revision
        Returns new state dict (for POST /api/config/provider).
        """
        # Idempotency check (fast path)
        if idempotency_key:
            cached = self._idem_cache.get(idempotency_key)
            if cached and (time.time() - cached[0] <= self._IDEMPOTENCY_TTL):
                logger.info("ConfigManager: idempotent switch hit (%s) — returning cached result.", idempotency_key)
                return dict(cached[1])  # shallow copy

        # Validate provider/mode
        provider = (provider or "").strip().lower()
        mode = (mode or "").strip().upper()

        if provider not in [p.lower() for p in self._available]:
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
                return dict(state)

            logger.info(
                "ConfigManager: switching provider — %s/%s → %s/%s",
                self._state.active, self._state.mode, provider, mode
            )
            self._ready.clear()

            # 1) Stop strategies
            try:
                if self._hook_stop_all_strategies:
                    await self._hook_stop_all_strategies()
                else:
                    raise RuntimeError("stop_all_strategies hook is not set")
            except Exception:
                logger.exception("ConfigManager: stop_all_strategies failed (continuing).")

            # 2) Stop streams
            try:
                if self._hook_stop_streams:
                    await self._hook_stop_streams()
                else:
                    raise RuntimeError("stop_streams hook is not set")
            except Exception:
                logger.exception("ConfigManager: stop_streams failed (continuing).")

            # 3) Reset book tracker
            try:
                if self._hook_reset_book_tracker:
                    self._hook_reset_book_tracker()
                else:
                    raise RuntimeError("reset_book_tracker hook is not set")
            except Exception:
                logger.exception("ConfigManager: reset_book_tracker failed (continuing).")

            # Update target config
            self._state.active = provider
            self._state.mode = mode

            # 4) Start streams for new provider/mode
            ws_enabled = False
            try:
                if self._hook_start_streams:
                    ws_enabled = await self._hook_start_streams(provider, mode)
                else:
                    raise RuntimeError("start_streams hook is not set")
            except Exception:
                logger.exception("ConfigManager: start_streams failed — WS disabled.")
                ws_enabled = False

            # 5) Bump revision & mark ready
            self._state.ws_enabled = bool(ws_enabled)
            self._state.revision += 1
            self._ready.set()

            state = self._state.to_dict()
            logger.info("ConfigManager: switched — %s", state)

            # Store idempotent result
            if idempotency_key:
                self._idem_cache[idempotency_key] = (time.time(), state)

            # Periodically prune cache
            self._prune_idem_cache()

            return dict(state)

    # ───────────────────────────── Internals ───────────────────────────────

    def _prune_idem_cache(self) -> None:
        now = time.time()
        stale_keys = [k for k, (ts, _) in self._idem_cache.items() if now - ts > self._IDEMPOTENCY_TTL]
        for k in stale_keys:
            self._idem_cache.pop(k, None)


# Module-level singleton (import this from other modules)
config_manager = ConfigManager()
