# app/config/settings.py
import os
from typing import List, Any, Tuple, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, AliasChoices


def _normalize_cors(v: Any) -> list[str]:
    """
    Accepts:
      - "*"                          -> ["*"] (reflect any Origin)
      - "a,b,c"                      -> ["a","b","c"]
      - '["a","b"]' or "[a,b]"      -> ["a","b"] (very naive JSON-ish split)
      - list/tuple                   -> list[str]
      - None/""                      -> []
    Strips whitespace and drops empties.
    """
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if s == "*":
            return ["*"]
        # Allow JSON-ish list: [a,b] or ["a","b"]
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        return [p.strip() for p in s.split(",") if p.strip()]
    # Anything else → best-effort string
    return [str(v).strip()] if str(v).strip() else []


def _csv_split(s: str | None) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


class Settings(BaseSettings):
    """
    Centralized runtime configuration.
    - Reads from .env (UTF-8) and environment variables
    - Ignores unknown keys (keeps config robust across branches)
    - Provides handy properties for CSV fields
    """
    # ---------- Pydantic / .env ----------
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ========== DB ==========
    database_url: str = Field(
        default=os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
        description="SQLAlchemy database URL (e.g., sqlite:///./app.db or postgres://...)",
    )
    sql_echo: bool = Field(
        default=os.getenv("SQL_ECHO", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("SQL_ECHO", "sql_echo"),
        description="Echo SQL statements (debug).",
    )

    # ========== Active selection (new; overrides legacy when set) ==========
    active_provider_env: str = Field(
        default=os.getenv("ACTIVE_PROVIDER", ""),
        validation_alias=AliasChoices("ACTIVE_PROVIDER", "active_provider"),
        description="Preferred: MEXC | BINANCE | GATE (overrides EXCHANGE_PROVIDER if set)",
    )
    active_mode_env: str = Field(
        default=os.getenv("ACTIVE_MODE", ""),
        validation_alias=AliasChoices("ACTIVE_MODE", "active_mode"),
        description="Preferred: PAPER | DEMO | LIVE (overrides ACCOUNT_MODE if set)",
    )

    # ========== Core / Provider & Mode (legacy keys still supported) ==========
    # Back-compat: MODE (paper|live) остаётся, но приоритет у ACCOUNT_MODE / ACTIVE_MODE
    # ACCOUNT_MODE: PAPER | LIVE | DEMO
    account_mode: str = Field(
        default=(os.getenv("ACCOUNT_MODE", os.getenv("MODE", "paper")) or "paper").lower(),
        validation_alias=AliasChoices("ACCOUNT_MODE", "account_mode", "MODE", "mode"),
        description="paper | live | demo",
    )
    # EXCHANGE_PROVIDER: MEXC | BINANCE (по умолчанию MEXC); now also GATE
    exchange_provider: str = Field(
        default=(os.getenv("EXCHANGE_PROVIDER", "MEXC") or "MEXC").upper(),
        validation_alias=AliasChoices("EXCHANGE_PROVIDER", "exchange_provider"),
        description="MEXC | BINANCE | GATE",
    )

    # Single-workspace for now (easy to expand later)
    workspace_id: int = Field(
        default=int(os.getenv("WORKSPACE_ID", "1")),
        validation_alias=AliasChoices("WORKSPACE_ID", "workspace_id"),
        description="Numeric workspace identifier (single-tenant for now)",
    )

    # ========== MEXC keys/urls ==========
    api_key: str = Field(
        default=os.getenv("MEXC_API_KEY", ""),
        validation_alias=AliasChoices("MEXC_API_KEY", "mexc_api_key"),
    )
    api_secret: str = Field(
        default=os.getenv("MEXC_API_SECRET", ""),
        validation_alias=AliasChoices("MEXC_API_SECRET", "mexc_api_secret"),
    )

    # ========== Binance (Demo/Live) ==========
    binance_api_key: str | None = Field(
        default=os.getenv("BINANCE_API_KEY"),
        validation_alias=AliasChoices("BINANCE_API_KEY", "binance_api_key"),
    )
    binance_api_secret: str | None = Field(
        default=os.getenv("BINANCE_API_SECRET"),
        validation_alias=AliasChoices("BINANCE_API_SECRET", "binance_api_secret"),
    )
    binance_rest_base: str = Field(
        default=os.getenv("BINANCE_REST_BASE", "https://testnet.binance.vision"),
        validation_alias=AliasChoices("BINANCE_REST_BASE", "binance_rest_base"),
        description="Binance REST base (demo by default)",
    )
    binance_ws_base: str = Field(
        default=os.getenv("BINANCE_WS_BASE", "wss://testnet.binance.vision/ws"),
        validation_alias=AliasChoices("BINANCE_WS_BASE", "binance_ws_base"),
        description="Binance WS base (demo by default)",
    )

    # ========== Gate (Live) ==========
    gate_api_key: str | None = Field(default=os.getenv("GATE_API_KEY"),
                                     validation_alias=AliasChoices("GATE_API_KEY", "gate_api_key"))
    gate_api_secret: str | None = Field(default=os.getenv("GATE_API_SECRET"),
                                        validation_alias=AliasChoices("GATE_API_SECRET", "gate_api_secret"))
    gate_rest_base: str = Field(default=os.getenv("GATE_REST_BASE", "https://api.gateio.ws/api/v4"),
                                validation_alias=AliasChoices("GATE_REST_BASE", "gate_rest_base"))
    gate_ws_base: str = Field(default=os.getenv("GATE_WS_BASE", "wss://api.gateio.ws/ws/v4/"),
                              validation_alias=AliasChoices("GATE_WS_BASE", "gate_ws_base"))

    # ========== Gate (Testnet) ==========
    gate_testnet_api_key: str | None = Field(default=os.getenv("GATE_TESTNET_API_KEY"))
    gate_testnet_api_secret: str | None = Field(default=os.getenv("GATE_TESTNET_API_SECRET"))
    gate_testnet_rest_base: str = Field(
        default=os.getenv("GATE_TESTNET_REST_BASE", "https://api-testnet.gateapi.io/api/v4")
    )
    gate_testnet_ws_base: str = Field(
        default=os.getenv("GATE_TESTNET_WS_BASE", "wss://ws-testnet.gate.com/v4/ws/spot")
    )

    # Optional UI id (tracing/WS)
    ui_id: str = Field(
        default=os.getenv("UI_ID", ""),
        validation_alias=AliasChoices("UI_ID", "U_ID", "u_id", "uiid"),
    )

    # ========== CSVs ==========
    symbols_csv: str = Field(
        default=os.getenv("SYMBOLS", "ATHUSDT,HBARUSDT"),
        validation_alias=AliasChoices("SYMBOLS", "symbols"),
    )

    # Accept CORS from env under either key; we’ll normalize below.
    cors_origins_env: str | list[str] = Field(
        default=os.getenv("CORS_ORIGINS", os.getenv("cors_origins", "")),
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins"),
    )

    # Back-compat: if someone sets this older key, we’ll merge it in.
    cors_origins_csv: str = Field(
        default=os.getenv("CORS_ORIGINS_CSV", "http://localhost:5173,http://localhost:3000"),
        validation_alias=AliasChoices("CORS_ORIGINS_CSV", "cors_origins_csv"),
    )

    # ========== HTTP (REST / PS fallback) — legacy MEXC defaults ==========
    rest_base_url: str = Field(
        default=os.getenv("REST_BASE_URL", "https://api.mexc.com/api/v3"),
        validation_alias=AliasChoices("REST_BASE_URL", "rest_base_url"),
        description="MEXC REST base URL (legacy field)",
    )
    poll_interval_sec: float = Field(
        default=float(os.getenv("POLL_INTERVAL_SEC", "1.5")),
        validation_alias=AliasChoices("POLL_INTERVAL_SEC", "poll_interval_sec"),
        description="HTTP/PS poll interval when WS is disabled/unavailable",
    )
    depth_limit: int = Field(
        default=int(os.getenv("DEPTH_LIMIT", "20")),
        validation_alias=AliasChoices("DEPTH_LIMIT", "depth_limit"),
        description="Depth limit for HTTP orderbook snapshots (if used)",
    )

    # ========== WebSocket (public) — legacy MEXC defaults ==========
    ws_url_public: str = Field(
        default=os.getenv("WS_URL_PUBLIC", "wss://wbs-api.mexc.com/ws"),
        validation_alias=AliasChoices("WS_URL_PUBLIC", "ws_url_public"),
        description="MEXC public WS URL (legacy field)",
    )
    ws_open_timeout: int = Field(
        default=int(os.getenv("WS_OPEN_TIMEOUT", "20")),
        validation_alias=AliasChoices("WS_OPEN_TIMEOUT", "ws_open_timeout"),
    )
    ws_close_timeout: int = Field(
        default=int(os.getenv("WS_CLOSE_TIMEOUT", "5")),
        validation_alias=AliasChoices("WS_CLOSE_TIMEOUT", "ws_close_timeout"),
    )

    # Optional DNS override (connect to IP with SNI=hostname)
    ws_dns_override: str = Field(
        default=os.getenv("WS_DNS_OVERRIDE", ""),
        validation_alias=AliasChoices("WS_DNS_OVERRIDE", "ws_dns_override"),
    )
    ws_server_hostname: str = Field(
        default=os.getenv("WS_SERVER_HOSTNAME", "wbs-api.mexc.com"),
        validation_alias=AliasChoices("WS_SERVER_HOSTNAME", "ws_server_hostname"),
    )

    # ========== Proxies (diag only) ==========
    http_proxy_env: str = Field(
        default=os.getenv("HTTP_PROXY", ""),
        validation_alias=AliasChoices("HTTP_PROXY", "http_proxy"),
    )
    https_proxy_env: str = Field(
        default=os.getenv("HTTPS_PROXY", ""),
        validation_alias=AliasChoices("HTTPS_PROXY", "https_proxy"),
    )
    no_proxy_env: str = Field(
        default=os.getenv("NO_PROXY", ""),
        validation_alias=AliasChoices("NO_PROXY", "no_proxy"),
    )

    # ========== Feature Flags ==========
    enable_ws: bool = Field(
        default=os.getenv("ENABLE_WS", "true").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("ENABLE_WS", "enable_ws"),
        description="Enable public WS client for quotes/orderbook sources",
    )
    enable_http_poller: bool = Field(
        default=os.getenv("ENABLE_HTTP_POLLER", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("ENABLE_HTTP_POLLER", "enable_http_poller"),
        description="Enable REST poller as a source",
    )
    enable_ps_poller: bool = Field(
        default=os.getenv("ENABLE_PS_POLLER", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("ENABLE_PS_POLLER", "enable_ps_poller"),
        description="Enable pseudo-snapshot poller (fallback)",
    )

    enable_ui_state: bool = Field(
        default=os.getenv("ENABLE_UI_STATE", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("ENABLE_UI_STATE", "enable_ui_state"),
        description="Persist and serve UI watchlist/layout/state from backend",
    )
    enable_sse_last_event_id: bool = Field(
        default=os.getenv("ENABLE_SSE_LAST_EVENT_ID", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("ENABLE_SSE_LAST_EVENT_ID", "enable_sse_last_event_id"),
        description="Support Last-Event-ID resumability for SSE stream",
    )
    enable_orderbook_ws: bool = Field(
        default=os.getenv("ENABLE_ORDERBOOK_WS", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("ENABLE_ORDERBOOK_WS", "enable_orderbook_ws"),
        description="Expose WS endpoint for L2 orderbook (snapshot+delta)",
    )

    # ========== Idempotency / Safety ==========
    idempotency_window_sec: int = Field(
        default=int(os.getenv("IDEMPOTENCY_WINDOW_SEC", "600")),
        validation_alias=AliasChoices("IDEMPOTENCY_WINDOW_SEC", "idempotency_window_sec"),
        description="How long to keep idempotency keys for trade/strategy commands",
    )

    # ========== UI State defaults / limits ==========
    ui_revision_seed: int = Field(
        default=int(os.getenv("UI_REVISION_SEED", "1")),
        validation_alias=AliasChoices("UI_REVISION_SEED", "ui_revision_seed"),
        description="Initial revision number starting point for UI state",
    )
    max_watchlist_bulk: int = Field(
        default=int(os.getenv("MAX_WATCHLIST_BULK", "50")),
        validation_alias=AliasChoices("MAX_WATCHLIST_BULK", "max_watchlist_bulk"),
        description="Max symbols accepted by bulk watchlist endpoint",
    )
    start_after_add_default: bool = Field(
        default=os.getenv("START_AFTER_ADD_DEFAULT", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("START_AFTER_ADD_DEFAULT", "start_after_add_default"),
        description="If true, newly added symbols can auto-start strategy (override per request)",
    )

    # Risk guard
    max_exposure_usd: float = Field(
        default=float(os.getenv("MAX_EXPOSURE_USD", "1000")),
        validation_alias=AliasChoices("MAX_EXPOSURE_USD", "max_exposure_usd"),
        description="Global guardrail for total exposure (paper/live)",
    )

    # ========== SSE tuning ==========
    sse_ping_interval_ms: int = Field(
        default=int(os.getenv("SSE_PING_INTERVAL_MS", "15000")),
        validation_alias=AliasChoices("SSE_PING_INTERVAL_MS", "sse_ping_interval_ms"),
        description="Heartbeat/ping interval to keep proxies from closing SSE",
    )
    sse_retry_base_ms: int = Field(
        default=int(os.getenv("SSE_RETRY_BASE_MS", "1000")),
        validation_alias=AliasChoices("SSE_RETRY_BASE_MS", "sse_retry_base_ms"),
        description="Client-side suggested retry (base) for EventSource",
    )
    sse_retry_max_ms: int = Field(
        default=int(os.getenv("SSE_RETRY_MAX_MS", "20000")),
        validation_alias=AliasChoices("SSE_RETRY_MAX_MS", "sse_retry_max_ms"),
        description="Client-side suggested max retry backoff for EventSource",
    )

    # ========== WS Orderbook tuning ==========
    ws_orderbook_snapshot_levels: int = Field(
        default=int(os.getenv("WS_OB_SNAPSHOT_LEVELS", "10")),
        validation_alias=AliasChoices("WS_OB_SNAPSHOT_LEVELS", "ws_ob_snapshot_levels"),
        description="How many levels per side to include in a snapshot",
    )
    ws_orderbook_delta_buffer: int = Field(
        default=int(os.getenv("WS_OB_DELTA_BUFFER", "64")),
        validation_alias=AliasChoices("WS_OB_DELTA_BUFFER", "ws_ob_delta_buffer"),
        description="Max buffered deltas before forcing a full snapshot",
    )

    # ========== Live convenience ==========
    api_key_header: str = Field(
        default=os.getenv("API_KEY_HEADER", ""),
        validation_alias=AliasChoices("API_KEY_HEADER", "api_key_header"),
    )
    recv_window_ms: int = Field(
        default=int(os.getenv("RECV_WINDOW_MS", "5000")),
        validation_alias=AliasChoices("RECV_WINDOW_MS", "recv_window_ms"),
    )

    # ========== Debug toggles ==========
    ws_debug_json_parity: bool = Field(
        default=os.getenv("WS_DEBUG_JSON_PARITY", "0").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("WS_DEBUG_JSON_PARITY", "ws_debug_json_parity"),
    )
    ws_debug_pb_variants: bool = Field(
        default=os.getenv("WS_DEBUG_PB_VARIANTS", "0").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("WS_DEBUG_PB_VARIANTS", "ws_debug_pb_variants"),
    )

    # ---------- Handy properties ----------
    @property
    def symbols(self) -> List[str]:
        return [s.upper() for s in _csv_split(self.symbols_csv)]

    @property
    def cors_origins(self) -> List[str]:
        """
        Final, normalized list the rest of the app should use.
        Priority:
          1) CORS_ORIGINS (env) if present (supports "*", CSV, JSON-ish, list)
          2) cors_origins_csv (CSV string)
        """
        env_list = _normalize_cors(self.cors_origins_env)
        if env_list:
            return env_list
        csv_list = _normalize_cors(self.cors_origins_csv)
        return csv_list

    # ---------- Active provider/mode resolution (new) ----------
    @property
    def active_provider(self) -> str:
        # ACTIVE_PROVIDER (if set) overrides legacy EXCHANGE_PROVIDER
        base = (self.active_provider_env or self.exchange_provider or "MEXC").strip().upper()
        if base not in {"MEXC", "BINANCE", "GATE"}:
            base = "MEXC"
        return base

    @property
    def active_mode(self) -> str:
        # ACTIVE_MODE (if set) overrides legacy ACCOUNT_MODE
        base = (self.active_mode_env or self.account_mode or "paper").strip().upper()
        if base not in {"PAPER", "DEMO", "LIVE"}:
            base = "PAPER"
        return base

    # Convenience flags (keep old ones for compatibility)
    @property
    def is_binance(self) -> bool:
        return self.active_provider == "BINANCE"

    @property
    def is_mexc(self) -> bool:
        return self.active_provider == "MEXC"

    @property
    def is_gate(self) -> bool:
        return self.active_provider == "GATE"

    @property
    def is_demo(self) -> bool:
        return self.active_mode == "DEMO"

    @property
    def is_paper(self) -> bool:
        return self.active_mode == "PAPER"

    @property
    def is_live(self) -> bool:
        return self.active_mode == "LIVE"

    # ---------- Provider-resolved endpoints & keys ----------
    @property
    def rest_base_url_resolved(self) -> str:
        """Provider/mode-aware REST base."""
        if self.is_gate:
            return self.gate_testnet_rest_base if self.is_demo else self.gate_rest_base
        if self.is_binance:
            return self.binance_rest_base
        # default MEXC
        return self.rest_base_url

    @property
    def ws_base_url_resolved(self) -> str:
        """Provider/mode-aware WS base (public market stream)."""
        if self.is_gate:
            return self.gate_testnet_ws_base if self.is_demo else self.gate_ws_base
        if self.is_binance:
            return self.binance_ws_base
        # default MEXC
        return self.ws_url_public

    def api_key_pair(self) -> Tuple[Optional[str], Optional[str]]:
        """Return (api_key, api_secret) for the *active* provider/mode."""
        if self.is_gate:
            if self.is_demo:
                return self.gate_testnet_api_key, self.gate_testnet_api_secret
            return self.gate_api_key, self.gate_api_secret
        if self.is_binance:
            return self.binance_api_key, self.binance_api_secret
        # MEXC
        return self.api_key, self.api_secret


settings = Settings()
