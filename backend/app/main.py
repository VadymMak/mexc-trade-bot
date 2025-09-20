# app/main.py
from __future__ import annotations

from typing import Any, Sequence, cast
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response as FastAPIResponse

# Prometheus is optional
try:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    _PROM_AVAILABLE = True
except Exception:
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    _PROM_AVAILABLE = False

from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.datastructures import Headers, MutableHeaders

from app.config.settings import settings
from app.api import routes as api_routes
from app.services.book_tracker import on_book_ticker

# 🔑 Ensure DB schema exists on startup (dev convenience; Alembic in prod)
from app.db.engine import engine
from app.models.base import Base

# !!! ВАЖНО: регистрируем ВСЕ модели до create_all, иначе таблицы не появятся
import app.models.ui_state          # noqa: F401
import app.models.strategy_state    # noqa: F401
import app.models.orders            # noqa: F401
import app.models.positions         # noqa: F401
import app.models.fills             # noqa: F401
import app.models.sessions          # noqa: F401

from sqlalchemy import inspect

APP_VERSION = "0.1.0"


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
        data = {"mode": getattr(settings, "mode", None), "symbols": getattr(settings, "symbols", [])}
    for key in ("api_key", "api_secret"):
        if key in data and data[key]:
            data[key] = "****"
    return data

def _is_ws_running(app: FastAPI) -> bool:
    task = getattr(app.state, "ws_task", None)
    return bool(task) and not task.done()

def _is_ps_running(app: FastAPI) -> bool:
    poller = getattr(app.state, "ps_poller", None)
    if not poller:
        return False
    for attr in ("running", "is_running", "started"):
        val = getattr(poller, attr, None)
        if isinstance(val, bool):
            return val
    return True


# ------------------------------ lifespan ------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB schema exists (dev convenience; use Alembic in prod)
    try:
        url_repr = str(getattr(engine.url, "render_as_string", lambda **_: engine.url)())
        print(f"🗄️  Using database: {url_repr}")
        Base.metadata.create_all(bind=engine)
        insp = inspect(engine)
        tables = sorted(insp.get_table_names())
        print(f"📦 DB schema ensured (create_all). Tables: {tables}")
        if "fills" not in tables:
            print("⚠️  WARNING: 'fills' table is missing after create_all — проверьте импорты моделей и Base.metadata.")
    except Exception as e:
        print(f"⚠️ DB schema init failed: {e}")

    app.state.ws_client = cast(Any | None, None)
    app.state.ws_task = cast(asyncio.Task | None, None)
    app.state.ps_poller = cast(Any | None, None)

    symbols = getattr(settings, "symbols", []) or []
    enable_ws = getattr(settings, "enable_ws", False)
    enable_ps = getattr(settings, "enable_ps_poller", True)

    print(f"🧭 Startup config: ENABLE_WS={enable_ws} | ENABLE_PS_POLLER={enable_ps} | symbols={symbols}")

    if enable_ws and _symbols_ok(symbols):
        try:
            from app.market_data.ws_client import MEXCWebSocketClient
            app.state.ws_client = MEXCWebSocketClient([s for s in symbols if str(s).strip()])
            app.state.ws_task = asyncio.create_task(app.state.ws_client.run())
            print("✅ WS market client started.")
        except Exception as e:
            print(f"❌ Failed to start WS client: {e}")
            print("↪️ Will try PS poller (if enabled).")
            app.state.ws_client = None
            app.state.ws_task = None
    elif enable_ws and not _symbols_ok(symbols):
        print("⚠️ ENABLE_WS=true, but symbols are empty — WS not started.")

    if app.state.ws_client is None and enable_ps:
        try:
            from app.market_data.http_client_ps import PSMarketPoller
            app.state.ps_poller = PSMarketPoller(
                symbols=symbols,
                interval=getattr(settings, "poll_interval_sec", 2.0),
                depth_limit=getattr(settings, "depth_limit", 10),
                on_update=_rest_update_adapter,
            )
            await app.state.ps_poller.start()
            print("⚠️ Using PS market poller (WS disabled or failed to start).")
        except Exception as e:
            print(f"⚠️ PS poller import/start failed, skipping: {e}")
            app.state.ps_poller = None

    print("🚀 Application startup complete.")
    try:
        yield
    finally:
        if app.state.ps_poller:
            with suppress(Exception):
                await app.state.ps_poller.stop()
            app.state.ps_poller = None

        if app.state.ws_client:
            with suppress(Exception):
                await app.state.ws_client.stop()

        await _cancel_and_await(app.state.ws_task, timeout=3.0)
        app.state.ws_task = None
        app.state.ws_client = None
        print("🛑 Application shutdown complete.")


# ------------------------------ app setup -----------------------------------
app = FastAPI(title="MEXC Trade Bot API", version=APP_VERSION, lifespan=lifespan)

# --- CORS (DEV-BULLETPROOF) --------------------------------------------------
def _parse_origins(raw) -> list[str]:
    """
    Accepts:
      - list/tuple of strings
      - JSON array string: '["http://localhost:5173"]'
      - comma-separated string: 'http://localhost:5173,http://127.0.0.1:5173'
      - '*' (wildcard)
    Returns a clean list (may contain '*').
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s == "*":
            return ["*"]
        # try JSON array first
        if s.startswith("[") and s.endswith("]"):
            try:
                import json
                arr = json.loads(s)
                if isinstance(arr, list):
                    return [str(x).strip() for x in arr if str(x).strip()]
            except Exception:
                pass
        # fallback: comma-separated
        return [p.strip() for p in s.split(",") if p.strip()]
    # anything else -> string repr
    return [str(raw).strip()] if str(raw).strip() else []

_configured_origins = _parse_origins(getattr(settings, "cors_origins", None))

DEFAULT_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
ALLOWED_ORIGINS = _configured_origins or DEFAULT_DEV_ORIGINS
_ALLOW_ALL = "*" in ALLOWED_ORIGINS

print(f"🌐 CORS origins: {_configured_origins or '(default dev list)'} | ALLOW_ALL={_ALLOW_ALL}")

class RawCORSMiddleware:
    """
    ASGI-level CORS middleware that:
      - Answers preflight OPTIONS before routers run.
      - Injects ACAO/ACAC on every response (including streaming/SSE).
    Keep this FIRST in the stack so nothing can bypass it.
    """
    def __init__(self, app: ASGIApp, allowed_origins: list[str], allow_all: bool = False):
        self.app = app
        self.allowed = set(allowed_origins)
        self.allow_all = allow_all or ("*" in self.allowed)

    def _origin_ok(self, origin: str | None) -> bool:
        if not origin:
            return False
        return self.allow_all or (origin in self.allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        headers = Headers(raw=scope.get("headers", []))
        origin = headers.get("origin")

        # Preflight: short-circuit with 204 + CORS headers
        if method == "OPTIONS" and self._origin_ok(origin):
            allow_headers = headers.get("access-control-request-headers") or (
                "Content-Type, Authorization, If-Match, X-Idempotency-Key, Accept, Cache-Control"
            )
            allow_methods = "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"
            allow_origin_value = origin or "*"

            await send({
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", allow_origin_value.encode()),
                    (b"access-control-allow-credentials", b"true"),
                    (b"access-control-allow-methods", allow_methods.encode()),
                    (b"access-control-allow-headers", allow_headers.encode()),
                    (b"access-control-max-age", b"86400"),
                    (b"vary", b"Origin"),
                ],
            })
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        # Wrap send() to inject ACAO on *every* response start
        async def send_wrapper(message):
            if message["type"] == "http.response.start" and self._origin_ok(origin):
                mh = MutableHeaders(raw=message.setdefault("headers", []))
                allow_origin_value = origin or "*"
                if "access-control-allow-origin" not in mh:
                    mh.append("Access-Control-Allow-Origin", allow_origin_value)
                    mh.append("Access-Control-Allow-Credentials", "true")
                    vary = mh.get("Vary")
                    if not vary:
                        mh.append("Vary", "Origin")
                    elif "Origin" not in vary:
                        mh["Vary"] = f"{vary}, Origin"
            await send(message)

        await self.app(scope, receive, send_wrapper)

# 0) Install the ASGI-level guard FIRST so it wraps everything (incl. StreamingResponse)
app.add_middleware(RawCORSMiddleware, allowed_origins=ALLOWED_ORIGINS, allow_all=_ALLOW_ALL)

# 1) Normal FastAPI CORS (nice defaults, header reflection, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if not _ALLOW_ALL else [],
    allow_origin_regex=".*" if _ALLOW_ALL else None,
    allow_credentials=True,  # cookie/auth flows from Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag", "Last-Event-ID", "Retry-After", "Location"],
    max_age=86400,
)

# 2) Router-level OPTIONS catch-all (extra safety)
@app.options("/{rest_of_path:path}")
async def _preflight_cors(rest_of_path: str, request: Request) -> FastAPIResponse:
    origin = request.headers.get("origin", "")
    acr_headers = request.headers.get("access-control-request-headers", "")
    headers: dict[str, str] = {}
    if (_ALLOW_ALL and origin) or (origin in ALLOWED_ORIGINS):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
        headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"
        headers["Access-Control-Allow-Headers"] = acr_headers or \
            "Content-Type, Authorization, If-Match, X-Idempotency-Key, Accept, Cache-Control"
        headers["Access-Control-Max-Age"] = "86400"
    return FastAPIResponse(status_code=204, headers=headers)


# ------------------------------ route mounting -------------------------------
# Mount the hub router (contains market/strategy/etc.)
app.include_router(api_routes.router)

# Also mount UI router here *safely* (only if not already mounted via api_routes)
def _mount_ui_router_if_needed(app: FastAPI) -> None:
    try:
        from app.routers import ui as ui_module
    except Exception as e:
        print(f"ℹ️ UI router import skipped: {e}")
        return

    ui_paths = {"/api/ui", "/api/ui/"}
    already = any(
        (getattr(r, "path", None) or "").startswith("/api/ui")
        for r in getattr(app, "routes", [])
    )
    if already:
        print("✅ UI router already mounted (skipping duplicate).")
        return

    try:
        app.include_router(ui_module.router)
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
    return {"ok": True, "name": "MEXC Trade Bot API", "version": APP_VERSION}


# ------------------------------ ops endpoints ------------------------------
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.get("/readyz")
async def readyz():
    ready = _is_ws_running(app) or _is_ps_running(app)
    return JSONResponse(status_code=200 if ready else 503, content={"ready": ready})

@app.get("/version")
async def version():
    return {"version": APP_VERSION}

@app.get("/debug/config")
async def debug_config():
    return _safe_settings_dict()

@app.get("/status")
async def status():
    ws_client = getattr(app.state, "ws_client", None)
    ws_connected = bool(getattr(ws_client, "_connected", False))
    ws_symbols = list(getattr(ws_client, "symbols", []) or [])
    ps_active = _is_ps_running(app)
    ps = getattr(app.state, "ps_poller", None)
    ps_symbols = list(getattr(ps, "symbols", []) or [])
    return {
        "version": APP_VERSION,
        "ws": {
            "enabled": getattr(settings, "enable_ws", False),
            "connected": ws_connected,
            "running": _is_ws_running(app),
            "symbols": ws_symbols,
        },
        "ps_poller": {
            "enabled": getattr(settings, "enable_ps_poller", True),
            "running": ps_active,
            "symbols": ps_symbols,
        },
    }

@app.get("/metrics")
async def metrics():
    if not _PROM_AVAILABLE:
        return PlainTextResponse(
            "prometheus_client not installed. Run: pip install prometheus-client",
            status_code=503,
        )
    output = generate_latest()
    return Response(content=output, media_type=CONTENT_TYPE_LATEST)
