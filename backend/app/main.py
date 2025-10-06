# app/main.py
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any, Sequence, cast, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

# Prometheus (optional)
try:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    _PROM_AVAILABLE = True
except Exception:
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    _PROM_AVAILABLE = False

from sqlalchemy import inspect

from app.config.settings import settings
from app.api import routes as api_routes
from app.services.book_tracker import on_book_ticker
from app.services.config_manager import config_manager

# Ensure DB schema exists on startup (dev convenience; Alembic in prod)
from app.db.engine import engine
from app.models.base import Base

# IMPORTANT: import ALL models before create_all so tables are known
import app.models.ui_state           # noqa: F401
import app.models.strategy_state     # noqa: F401
import app.models.orders             # noqa: F401
import app.models.positions          # noqa: F401
import app.models.fills              # noqa: F401
import app.models.sessions           # noqa: F401
import app.models.pnl_ledger         # noqa: F401
import app.models.pnl_daily          # noqa: F401

APP_VERSION = "0.1.0"
logger = logging.getLogger("app.main")


# ------------------------------- helpers ------------------------------------
async def _rest_update_adapter(symbol: str, last: float | None, bid: float | None, ask: float | None) -> None:
    b = float(bid or 0.0)
    a = float(ask or 0.0)
    await on_book_ticker(symbol=symbol, bid=b, bid_qty=0.0, ask=a, ask_qty=0.0, ts_ms=None)


def _symbols_ok(syms: Sequence[str] | None) -> bool:
    return bool(syms) and any(str(s or "").strip() for s in (syms or []))


async def _cancel_and_await(task: asyncio.Task | None, timeout: float = 3.0) -> None:
    if not task:
        return
    if task.done():
        with suppress(Exception):
            await task
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        with suppress(Exception):
            await asyncio.gather(task, return_exceptions=True)


def _safe_settings_dict() -> dict[str, Any]:
    try:
        data = settings.model_dump()
    except Exception:
        data = {
            "mode": getattr(settings, "active_mode", None),
            "symbols": getattr(settings, "symbols", []),
        }
    # mask obvious secrets
    for key in list(data.keys()):
        lk = key.lower()
        if "secret" in lk or ("key" in lk and "base" not in lk):
            if data.get(key):
                data[key] = "****"
    return data


# ------------------------------ lifespan ------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB schema exists (dev convenience; use Alembic in prod)
    try:
        url_repr = str(getattr(engine.url, "render_as_string", lambda **_: engine.url)(hide_password=True))  # type: ignore[arg-type]
        print(f"🗄️  Using database: {url_repr}")
        Base.metadata.create_all(bind=engine)
        insp = inspect(engine)
        tables = sorted(insp.get_table_names())
        print(f"📦 DB schema ensured (create_all). Tables: {tables}")
        if "pnl_ledger" not in tables or "pnl_daily" not in tables:
            print("⚠️  WARNING: PnL tables missing — check model imports and Base.metadata registration.")
    except Exception as e:
        print(f"⚠️ DB schema init failed: {e}")

    # State holders
    app.state.ws_client = cast(Optional[Any], None)
    app.state.ws_task = cast(Optional[asyncio.Task], None)
    app.state.ps_poller = cast(Optional[Any], None)

    symbols = getattr(settings, "symbols", []) or []
    enable_ws = getattr(settings, "enable_ws", False)
    enable_ps = getattr(settings, "enable_ps_poller", True)

    # Resolved provider/mode + REST base (initial)
    initial_provider = str(getattr(settings, "active_provider", getattr(settings, "exchange_provider", "MEXC"))).upper()
    initial_mode = str(getattr(settings, "active_mode", getattr(settings, "account_mode", "PAPER"))).upper()
    rest_base = getattr(settings, "rest_base_url_resolved", getattr(settings, "rest_base_url", ""))

    print(
        f"🧭 Startup config: PROVIDER={initial_provider} | MODE={initial_mode} | ENABLE_WS={enable_ws} | "
        f"ENABLE_PS_POLLER={enable_ps} | symbols={symbols} | REST_BASE={rest_base}"
    )

    # Import here to avoid circulars
    try:
        from app.services.book_tracker import ensure_symbols_subscribed
    except Exception as e:
        ensure_symbols_subscribed = None  # type: ignore
        print(f"⚠️ Cannot import ensure_symbols_subscribed: {e}")

    # ────────────────────── Hooks for ConfigManager ───────────────────────
    async def _hook_stop_all_strategies() -> None:
        try:
            from app.services.strategy_service import StrategyService  # type: ignore
            svc = getattr(StrategyService, "get", None)
            if callable(svc):
                inst = svc()
                stopper = getattr(inst, "stop_all", None) or getattr(inst, "stop_all_symbols", None)
                if stopper:
                    res = stopper()
                    if asyncio.iscoroutine(res):
                        await res
                    return
        except Exception as e:
            logger.warning(f"StrategyService stop_all failed/unknown: {e}")
        logger.info("No strategy stop hook found; continuing.")

    async def _hook_stop_streams() -> None:
        # PS poller
        if app.state.ps_poller:
            try:
                await app.state.ps_poller.stop()
            except Exception as e:
                logger.warning(f"PS poller stop error: {e}")
            finally:
                app.state.ps_poller = None
        # WS client
        if app.state.ws_client:
            with suppress(Exception):
                await app.state.ws_client.stop()
        await _cancel_and_await(app.state.ws_task, timeout=3.0)
        app.state.ws_task = None
        app.state.ws_client = None
        logger.info("Streams stopped.")

    def _hook_reset_book_tracker() -> None:
        try:
            from app.services import book_tracker as bt  # type: ignore
            reset_fn = getattr(bt, "reset", None)
            if callable(reset_fn):
                reset_fn()
                logger.info("Book tracker reset() called.")
                return
            clear_all = getattr(bt, "clear_all", None) or getattr(bt, "clear", None)
            if callable(clear_all):
                clear_all()
                logger.info("Book tracker clear() called.")
                return
            logger.info("Book tracker has no reset(); will re-seed by ensure_symbols_subscribed later.")
        except Exception as e:
            logger.warning(f"Book tracker reset failed: {e}")

    async def _hook_start_streams(provider: str, mode: str) -> bool:
        prov = (provider or "").strip().upper()
        ws_enabled_flag = False

        # Always try (re)subscription via service layer
        if ensure_symbols_subscribed and _symbols_ok(symbols):
            try:
                await ensure_symbols_subscribed(symbols)
            except Exception as e:
                logger.warning(f"ensure_symbols_subscribed failed: {e}")

        # MEXC WS
        if prov == "MEXC" and enable_ws and _symbols_ok(symbols):
            try:
                from app.market_data.ws_client import MEXCWebSocketClient
                app.state.ws_client = MEXCWebSocketClient([s for s in symbols if str(s).strip()])
                app.state.ws_task = asyncio.create_task(app.state.ws_client.run())
                logger.info("✅ WS market client started (MEXC).")
                ws_enabled_flag = True
            except Exception as e:
                logger.error(f"❌ Failed to start MEXC WS client: {e}")
                app.state.ws_client = None
                app.state.ws_task = None

        # GATE WS
        if prov == "GATE" and enable_ws and _symbols_ok(symbols) and app.state.ws_client is None:
            try:
                from app.market_data.gate_ws import GateWebSocketClient
                app.state.ws_client = GateWebSocketClient(
                    [s for s in symbols if str(s).strip()],
                    depth_limit=getattr(settings, "depth_limit", 10),
                    want_tickers=True,
                    want_order_book=True,
                )
                app.state.ws_task = asyncio.create_task(app.state.ws_client.run())
                logger.info("✅ WS market client started (GATE).")
                ws_enabled_flag = True
            except Exception as e:
                logger.error(f"❌ Failed to start GATE WS client: {e}")
                app.state.ws_client = None
                app.state.ws_task = None

        # (Optional) BINANCE WS placeholder — wire in when client is ready
        # if prov == "BINANCE" and enable_ws and _symbols_ok(symbols) and app.state.ws_client is None:
        #     from app.market_data.binance_ws import BinanceWebSocketClient
        #     ...

        # PS poller (fallback)
        if app.state.ws_client is None and getattr(settings, "enable_ps_poller", True):
            try:
                from app.market_data.http_client_ps import PSMarketPoller
                app.state.ps_poller = PSMarketPoller(
                    symbols=symbols,
                    interval=getattr(settings, "poll_interval_sec", 2.0),
                    depth_limit=getattr(settings, "depth_limit", 10),
                    on_update=_rest_update_adapter,
                )
                await app.state.ps_poller.start()
                logger.info("⚠️ Using PS market poller (WS disabled or not started).")
            except Exception as e:
                logger.error(f"⚠️ PS poller import/start failed, skipping: {e}")
                app.state.ps_poller = None

        return ws_enabled_flag

    # Wire hooks into ConfigManager
    config_manager.set_hooks(
        stop_all_strategies=_hook_stop_all_strategies,
        stop_streams=_hook_stop_streams,
        start_streams=_hook_start_streams,
        reset_book_tracker=_hook_reset_book_tracker,
    )

    # Initialize ConfigManager state and start initial streams
    config_manager._state.active = initial_provider  # type: ignore[attr-defined]
    config_manager._state.mode = initial_mode        # type: ignore[attr-defined]
    try:
        await config_manager.init_on_startup()
    except Exception as e:
        logger.error(f"ConfigManager init_on_startup failed: {e}")

    print("🚀 Application startup complete (managed by ConfigManager).")
    try:
        yield
    finally:
        with suppress(Exception):
            await _hook_stop_streams()
        print("🛑 Application shutdown complete.")


# ------------------------------ app setup -----------------------------------
# (Broaden title a bit — purely cosmetic)
app = FastAPI(title="Trade Bot API", version=APP_VERSION, lifespan=lifespan)

# ------------------------------ CORS ----------------------------------------
_allowed_origins = list(getattr(settings, "cors_origins", []) or []) or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# If wildcard origin is used, credentials must be False (browser restriction)
_allow_credentials = "*" not in _allowed_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "If-Match",
        "X-Idempotency-Key",
        "Accept",
        "Cache-Control",
    ],
)

# ------------------------------ route mounting ------------------------------
app.include_router(api_routes.router)

# --- Debug: list all routes at startup ---
def _print_routes(app_: FastAPI) -> None:
    try:
        print("🧭 Routes:")
        for r in getattr(app_, "routes", []):
            path = getattr(r, "path", None) or getattr(r, "path_format", None) or str(r)
            methods = ",".join(sorted(getattr(r, "methods", set())))
            print(f"  {methods or '-':<12} {path}")
    except Exception as e:
        print(f"⚠️ Route listing failed: {e}")

_print_routes(app)

# Optionally mount UI router here if not already included by api.routes
def _mount_ui_router_if_needed(app_: FastAPI) -> None:
    try:
        from app.routers import ui as ui_module
    except Exception as e:
        print(f"ℹ️ UI router import skipped: {e}")
        return
    already = any((getattr(r, "path", None) or "").startswith("/api/ui") for r in getattr(app_, "routes", []))
    if already:
        print("✅ UI router already mounted (skipping duplicate).")
        return
    try:
        app_.include_router(ui_module.router)
        print("✅ UI router mounted at /api/ui")
    except Exception as e:
        print(f"⚠️ Failed to mount UI router: {e}")

_mount_ui_router_if_needed(app)

# ------------------------------ basic endpoints ------------------------------
@app.get("/ping")
async def ping():
    return {"message": "pong"}

@app.get("/")
async def root():
    return {"ok": True, "name": "Trade Bot API", "version": APP_VERSION, "config": _safe_settings_dict()}

# --- Ultra simple health for request loop ---
@app.get("/__debug")
async def __debug():
    return {"ok": True}

# ------------------------------ ops: /metrics -------------------------------
if _PROM_AVAILABLE:
    @app.get("/metrics")
    async def metrics():
        try:
            data = generate_latest()  # type: ignore[no-untyped-call]
            return PlainTextResponse(data.decode("utf-8"), media_type=CONTENT_TYPE_LATEST)  # type: ignore[arg-type]
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
else:
    # Fallback so the route always exists (matches your OpenAPI)
    @app.get("/metrics")
    async def metrics_fallback():
        return PlainTextResponse("# metrics not enabled\n", media_type=CONTENT_TYPE_LATEST)
