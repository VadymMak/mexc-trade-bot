# app/config/settings.py
import os
from typing import List, Any, Tuple, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, AliasChoices


def _normalize_cors(v: Any) -> list[str]:
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
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        return [p.strip() for p in s.split(",") if p.strip()]
    s = str(v).strip()
    return [s] if s else []


def _csv_split(s: str | None) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


class Settings(BaseSettings):
    """
    Unified settings:
    - Keeps your aliases & provider/mode resolution.
    - Adds micro-scalp thresholds (ATR/spread/depth/ratio-gate), exec/risk, health, and sanity helpers.
    """
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

    # ========== Active selection ==========
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

    # ========== Core / legacy ==========
    account_mode: str = Field(
        default=(os.getenv("ACCOUNT_MODE", os.getenv("MODE", "paper")) or "paper").lower(),
        validation_alias=AliasChoices("ACCOUNT_MODE", "account_mode", "MODE", "mode"),
        description="paper | live | demo",
    )
    exchange_provider: str = Field(
        default=(os.getenv("EXCHANGE_PROVIDER", "MEXC") or "MEXC").upper(),
        validation_alias=AliasChoices("EXCHANGE_PROVIDER", "exchange_provider"),
        description="MEXC | BINANCE | GATE",
    )
    workspace_id: int = Field(
        default=int(os.getenv("WORKSPACE_ID", "1")),
        validation_alias=AliasChoices("WORKSPACE_ID", "workspace_id"),
        description="Numeric workspace identifier (single-tenant for now)",
    )

    # ---------------------------------------------------------
    # Global WS hard override (wins over everything if set)
    # ---------------------------------------------------------
    ws_base_url_override: str = Field(
        default=os.getenv("WS_BASE_URL_RESOLVED", ""),
        validation_alias=AliasChoices("WS_BASE_URL_RESOLVED", "ws_base_url_resolved"),
        description="If set, this URL is used for all WS providers (hard override).",
    )

    # ========== MEXC ==========
    api_key: str = Field(
        default=os.getenv("MEXC_API_KEY", os.getenv("MEXC_KEY", os.getenv("MEXC_APIKEY", ""))),
        validation_alias=AliasChoices("MEXC_API_KEY", "mexc_api_key", "MEXC_KEY", "MEXC_APIKEY"),
    )
    api_secret: str = Field(
        default=os.getenv("MEXC_API_SECRET", os.getenv("MEXC_SECRET", "")),
        validation_alias=AliasChoices("MEXC_API_SECRET", "mexc_api_secret", "MEXC_SECRET"),
    )
    mexc_rest_base: str = Field(
        default=os.getenv("MEXC_REST_BASE", "https://api.mexc.com"),
        validation_alias=AliasChoices("MEXC_REST_BASE", "mexc_rest_base"),
        description="MEXC REST base (Spot v3)",
    )
    # NEW: used by candles_cache in demo mode; defaults to prod if unset
    mexc_testnet_rest_base: str = Field(
        default=os.getenv("MEXC_TESTNET_REST_BASE", os.getenv("MEXC_REST_BASE", "https://api.mexc.com")),
        validation_alias=AliasChoices("MEXC_TESTNET_REST_BASE", "mexc_testnet_rest_base"),
        description="MEXC testnet REST base (falls back to prod if not provided)",
    )

    # ========== Binance ==========
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

    # ========== Gate ==========
    gate_api_key: str | None = Field(
        default=os.getenv("GATE_API_KEY"),
        validation_alias=AliasChoices("GATE_API_KEY", "gate_api_key"),
    )
    gate_api_secret: str | None = Field(
        default=os.getenv("GATE_API_SECRET"),
        validation_alias=AliasChoices("GATE_API_SECRET", "gate_api_secret"),
    )
    gate_rest_base: str = Field(
        default=os.getenv("GATE_REST_BASE", "https://api.gateio.ws/api/v4"),
        validation_alias=AliasChoices("GATE_REST_BASE", "gate_rest_base"),
    )
    gate_ws_base: str = Field(
        default=os.getenv("GATE_WS_BASE", "wss://api.gateio.ws/ws/v4/"),
        validation_alias=AliasChoices("GATE_WS_BASE", "gate_ws_base"),
    )

    # ---------- Gate explicit WS env switch ----------
    gate_ws_env: str = Field(
        default=os.getenv("GATE_WS_ENV", ""),
        validation_alias=AliasChoices("GATE_WS_ENV", "gate_ws_env"),
        description="LIVE | TESTNET for Gate WS (overrides global ACTIVE_MODE).",
    )

    # ========== Gate (Testnet) ==========
    gate_testnet_api_key: str | None = Field(default=os.getenv("GATE_TESTNET_API_KEY"))
    gate_testnet_api_secret: str | None = Field(default=os.getenv("GATE_TESTNET_API_SECRET"))
    gate_testnet_rest_base: str = Field(
        default=os.getenv("GATE_TESTNET_REST_BASE", "https://api-testnet.gateapi.io/api/v4"),
        validation_alias=AliasChoices("GATE_TESTNET_REST_BASE", "gate_testnet_rest_base"),
        description="Gate testnet REST (api-testnet.gateapi.io)",
    )
    gate_testnet_ws_base: str = Field(
        default=os.getenv("GATE_TESTNET_WS_BASE", "wss://ws-testnet.gate.com/v4/ws/spot"),
        validation_alias=AliasChoices("GATE_TESTNET_WS_BASE", "gate_testnet_ws_base"),
        description="Gate testnet WS (ws-testnet.gate.com/v4/ws/spot)",
    )

    # Optional UI id
    ui_id: str = Field(
        default=os.getenv("UI_ID", ""),
        validation_alias=AliasChoices("UI_ID", "U_ID", "u_id", "uiid"),
    )

    # ========== CSVs / symbols / quote ==========
    symbols_csv: str = Field(
        default=os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT"),
        validation_alias=AliasChoices("SYMBOLS", "symbols"),
    )
    quote: str = Field(
        default=os.getenv("QUOTE", "USDT"),
        validation_alias=AliasChoices("QUOTE", "quote"),
        description="Target quote currency for sanity checks",
    )

    # ========== CORS / proxies ==========
    cors_origins_env: str | list[str] = Field(
        default=os.getenv("CORS_ORIGINS", os.getenv("cors_origins", "")),
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins"),
    )
    cors_origins_csv: str = Field(
        default=os.getenv("CORS_ORIGINS_CSV", "http://localhost:5173,http://localhost:3000"),
        validation_alias=AliasChoices("CORS_ORIGINS_CSV", "cors_origins_csv"),
    )

    # ========== HTTP/WS (legacy MEXC) ==========
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
    ws_dns_override: str = Field(
        default=os.getenv("WS_DNS_OVERRIDE", ""),
        validation_alias=AliasChoices("WS_DNS_OVERRIDE", "ws_dns_override"),
    )
    ws_server_hostname: str = Field(
        default=os.getenv("WS_SERVER_HOSTNAME", "wbs-api.mexc.com"),
        validation_alias=AliasChoices("WS_SERVER_HOSTNAME", "ws_server_hostname"),
    )

    # ========== Proxies ==========
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

    # ========== Feature Flags (infra) ==========
    enable_ws: bool = Field(default=os.getenv("ENABLE_WS", "true").lower() in {"1", "true", "yes", "on"},
                            validation_alias=AliasChoices("ENABLE_WS", "enable_ws"))
    enable_http_poller: bool = Field(default=os.getenv("ENABLE_HTTP_POLLER", "false").lower() in {"1", "true", "yes", "on"},
                                     validation_alias=AliasChoices("ENABLE_HTTP_POLLER", "enable_http_poller"))
    enable_ps_poller: bool = Field(default=os.getenv("ENABLE_PS_POLLER", "true").lower() in {"1", "true", "yes", "on"},
                                   validation_alias=AliasChoices("ENABLE_PS_POLLER", "enable_ps_poller"))
    enable_ui_state: bool = Field(default=os.getenv("ENABLE_UI_STATE", "false").lower() in {"1", "true", "yes", "on"},
                                  validation_alias=AliasChoices("ENABLE_UI_STATE", "enable_ui_state"))
    enable_sse_last_event_id: bool = Field(default=os.getenv("ENABLE_SSE_LAST_EVENT_ID", "false").lower() in {"1", "true", "yes", "on"},
                                           validation_alias=AliasChoices("ENABLE_SSE_LAST_EVENT_ID", "enable_sse_last_event_id"))
    enable_orderbook_ws: bool = Field(default=os.getenv("ENABLE_ORDERBOOK_WS", "false").lower() in {"1", "true", "yes", "on"},
                                      validation_alias=AliasChoices("ENABLE_ORDERBOOK_WS", "enable_orderbook_ws"))

    # ========== Scanner Features / Weights / TTL ==========
    feature_vol_pattern_v1: bool = Field(default=os.getenv("FEATURE_VOL_PATTERN_V1", "0").lower() in {"1", "true", "yes", "on"},
                                         validation_alias=AliasChoices("FEATURE_VOL_PATTERN_V1", "feature_vol_pattern_v1"))
    feature_glass_bands: bool = Field(default=os.getenv("FEATURE_GLASS_BANDS", "0").lower() in {"1", "true", "yes", "on"},
                                      validation_alias=AliasChoices("FEATURE_GLASS_BANDS", "feature_glass_bands"))
    feature_time_aware: bool = Field(default=os.getenv("FEATURE_TIME_AWARE", "0").lower() in {"1", "true", "yes", "on"},
                                     validation_alias=AliasChoices("FEATURE_TIME_AWARE", "feature_time_aware"))
    exec_dry_run: bool = Field(default=os.getenv("EXEC_DRY_RUN", "1").lower() in {"1", "true", "yes", "on"},
                               validation_alias=AliasChoices("EXEC_DRY_RUN", "exec_dry_run"))

    # (legacy, kept) custom scoring knobs you already had
    sc_w_spread_bps: float = Field(default=float(os.getenv("SC_W_SPREAD_BPS", "15")))
    sc_w_sum5: float = Field(default=float(os.getenv("SC_W_SUM5", "18")))
    sc_w_ratio: float = Field(default=float(os.getenv("SC_W_RATIO", "18")))
    sc_w_volpat: float = Field(default=float(os.getenv("SC_W_VOLPAT", "18")))
    sc_w_stability: float = Field(default=float(os.getenv("SC_W_STABILITY", "18")))
    sc_w_spoof: float = Field(default=float(os.getenv("SC_W_SPOOF", "8")))
    sc_w_exit: float = Field(default=float(os.getenv("SC_W_EXIT", "8")))

    # NEW: weights expected by market_scanner._score_row (optional env overrides)
    score_w_usd_per_min: float = Field(default=float(os.getenv("SCORE_W_USD_PER_MIN", "1.0")))
    score_w_depth: float = Field(default=float(os.getenv("SCORE_W_DEPTH", "0.7")))
    score_w_spread: float = Field(default=float(os.getenv("SCORE_W_SPREAD", "0.5")))
    score_w_eff: float = Field(default=float(os.getenv("SCORE_W_EFF", "0.6")))
    score_w_vol_pattern: float = Field(default=float(os.getenv("SCORE_W_VOL_PATTERN", "0.4")))
    score_w_dca: float = Field(default=float(os.getenv("SCORE_W_DCA", "0.5")))
    score_w_atr: float = Field(default=float(os.getenv("SCORE_W_ATR", "0.3")))

    scanner_cache_ttl: float = Field(default=float(os.getenv("SCANNER_CACHE_TTL", "20.0")))

    # ======== Strategy thresholds (prompt) ========
    # Tape
    usdpm_min: float = Field(default=float(os.getenv("USDPM_MIN", "20.0")))
    tpm_min: int = Field(default=int(os.getenv("TPM_MIN", "5")))
    median_usd_min: float = Field(default=float(os.getenv("MEDIAN_USD_MIN", "0")))  # optional
    # ATR
    atr_max_usd: float = Field(default=float(os.getenv("ATR_MAX_USD", "8.0")))
    atr_pct_max_1m: float = Field(default=float(os.getenv("ATR_PCT_MAX_1M", "0.8")))  # %
    # Glass / spread / depth
    spread_bps_min: int = Field(default=int(os.getenv("SPREAD_BPS_MIN", "1")))
    spread_bps_max: int = Field(default=int(os.getenv("SPREAD_BPS_MAX", "7")))
    depth5_min_usd: int = Field(default=int(os.getenv("DEPTH5_MIN_USD", "1000")))
    depth10_min_usd: int = Field(default=int(os.getenv("DEPTH10_MIN_USD", "3000")))
    # Ratio-gate
    usdpm_per_depth5_min_ratio: float = Field(default=float(os.getenv("USDPM_PER_DEPTH5_MIN_RATIO", "0.1")))
    # Balance filters
    stability_min: int = Field(default=int(os.getenv("STABILITY_MIN", "70")))
    vol_pattern_min: int = Field(default=int(os.getenv("VOL_PATTERN_MIN", "0")))
    vol_pattern_max: int = Field(default=int(os.getenv("VOL_PATTERN_MAX", "100")))

    # ======== Exec limits / risk ========
    order_size_usd_min: float = Field(default=float(os.getenv("ORDER_SIZE_USD_MIN", "1.0")))
    order_size_usd_max: float = Field(default=float(os.getenv("ORDER_SIZE_USD_MAX", "2.0")))
    exec_cancel_timeout_sec: int = Field(default=int(os.getenv("EXEC_CANCEL_TIMEOUT_SEC", "10")))
    exec_tp_pct: float = Field(default=float(os.getenv("EXEC_TP_PCT", "0.1")))  # +0.1%
    max_exposure_usd: float = Field(default=float(os.getenv("MAX_EXPOSURE_USD", "1000")))
    idempotency_window_sec: int = Field(default=int(os.getenv("IDEMPOTENCY_WINDOW_SEC", "600")))

    # ======== Inclusion / Exclusions ========
    include_stables: bool = Field(default=os.getenv("INCLUDE_STABLES", "false").lower() in {"1", "true", "yes", "on"})
    exclude_leveraged: bool = Field(default=os.getenv("EXCLUDE_LEVERAGED", "true").lower() in {"1", "true", "yes", "on"})
    exclude_perps: bool = Field(default=os.getenv("EXCLUDE_PERPS", "true").lower() in {"1", "true", "yes", "on"})

    # ======== SSE / WS tuning ========
    sse_ping_interval_ms: int = Field(default=int(os.getenv("SSE_PING_INTERVAL_MS", "15000")))
    sse_retry_base_ms: int = Field(default=int(os.getenv("SSE_RETRY_BASE_MS", "1000")))
    sse_retry_max_ms: int = Field(default=int(os.getenv("SSE_RETRY_MAX_MS", "20000")))
    ws_orderbook_snapshot_levels: int = Field(default=int(os.getenv("WS_OB_SNAPSHOT_LEVELS", "10")))
    ws_orderbook_delta_buffer: int = Field(default=int(os.getenv("WS_OB_DELTA_BUFFER", "64")))

    # ======== Metrics / Health ========
    metrics_port: int = Field(default=int(os.getenv("METRICS_PORT", "9000")))
    health_ws_lag_ms_warn: int = Field(default=int(os.getenv("HEALTH_WS_LAG_MS_WARN", "1200")))

    # ======== Live convenience ========
    api_key_header: str = Field(default=os.getenv("API_KEY_HEADER", ""))
    recv_window_ms: int = Field(default=int(os.getenv("RECV_WINDOW_MS", "5000")))

    # ========== Debug toggles ==========
    ws_debug_json_parity: bool = Field(default=os.getenv("WS_DEBUG_JSON_PARITY", "0").lower() in {"1", "true", "yes", "on"})
    ws_debug_pb_variants: bool = Field(default=os.getenv("WS_DEBUG_PB_VARIANTS", "0").lower() in {"1", "true", "yes", "on"})

    # ---------- Handy properties ----------
    @property
    def symbols(self) -> List[str]:
        return [s.upper() for s in _csv_split(self.symbols_csv)]

    @property
    def live_use_market_for_maker(self) -> bool:
        raw = getattr(self, "LIVE_USE_MARKET_FOR_MAKER", os.getenv("LIVE_USE_MARKET_FOR_MAKER", "true"))
        return str(raw).lower() in {"1", "true", "yes", "on"}

    @property
    def cors_origins(self) -> List[str]:
        env_list = _normalize_cors(self.cors_origins_env)
        if env_list:
            return env_list
        return _normalize_cors(self.cors_origins_csv)

    @property
    def proxies(self) -> dict:
        proxies: dict = {}
        if self.http_proxy_env:
            proxies["http://"] = self.http_proxy_env
        https_val = self.https_proxy_env
        if https_val:
            proxies["https://"] = https_val
        return proxies

    # ---------- Active provider/mode ----------
    @property
    def active_provider(self) -> str:
        base = (self.active_provider_env or self.exchange_provider or "MEXC").strip().upper()
        if base not in {"MEXC", "BINANCE", "GATE"}:
            base = "MEXC"
        return base

    @property
    def active_mode(self) -> str:
        base = (self.active_mode_env or self.account_mode or "paper").strip().upper()
        if base not in {"PAPER", "DEMO", "LIVE"}:
            base = "PAPER"
        return base

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

    # ---------- Provider-resolved endpoints ----------
    @property
    def rest_base_url_resolved(self) -> str:
        # For Gate, honor explicit WS env for REST too (keeps demo/live consistent)
        if self.is_gate:
            env = (self.gate_ws_env or "").strip().upper()
            if env in {"TESTNET", "SANDBOX"}:
                return self.gate_testnet_rest_base
            if env == "LIVE":
                return self.gate_rest_base
            # Fallback to global ACTIVE_MODE if GATE_WS_ENV not set
            return self.gate_testnet_rest_base if self.is_demo else self.gate_rest_base
        if self.is_binance:
            return self.binance_rest_base
        # MEXC default
        return self.mexc_rest_base or self.rest_base_url

    @property
    def ws_base_url_resolved(self) -> str:
        # Global hard override wins
        if self.ws_base_url_override:
            return self.ws_base_url_override
        if self.is_gate:
            env = (self.gate_ws_env or "").strip().upper()
            if env in {"TESTNET", "SANDBOX"}:
                return self.gate_testnet_ws_base
            if env == "LIVE":
                return self.gate_ws_base
            # Fallback to global ACTIVE_MODE if GATE_WS_ENV not set
            return self.gate_testnet_ws_base if self.is_demo else self.gate_ws_base
        if self.is_binance:
            return self.binance_ws_base
        # MEXC default
        return self.ws_url_public

    def api_key_pair(self) -> Tuple[Optional[str], Optional[str]]:
        if self.is_gate:
            env = (self.gate_ws_env or "").strip().upper()
            if env in {"TESTNET", "SANDBOX"}:
                return self.gate_testnet_api_key, self.gate_testnet_api_secret
            if env == "LIVE":
                return self.gate_api_key, self.gate_api_secret
            if self.is_demo:
                return self.gate_testnet_api_key, self.gate_testnet_api_secret
            return self.gate_api_key, self.gate_api_secret
        if self.is_binance:
            return self.binance_api_key, self.binance_api_secret
        return self.api_key, self.api_secret

    # ---------- Compat: Gate WS url alias ----------
    @property
    def gate_ws_url(self) -> str:
        """Alias used by GateWebSocketClient; keeps your current client line working."""
        return self.gate_ws_base

    # ---------- Sanity helpers ----------
    def is_live_safe(self) -> bool:
        """LIVE=true requires valid keys for the active provider."""
        if not self.is_live:
            return True
        k, s = self.api_key_pair()
        return bool(k and s)

    def explain_sanity(self) -> list[str]:
        issues: list[str] = []
        if self.is_live and not self.is_live_safe():
            issues.append("LIVE=on, но не заданы ключи для активного провайдера.")
        if self.database_url.startswith("sqlite") and self.is_live:
            issues.append("LIVE на SQLite — проверь DATABASE_URL (ожидается Postgres).")
        if self.symbols and any(self.quote not in s for s in self.symbols):
            issues.append(f"В SYMBOLS есть пары без квоты {self.quote}.")
        if self.depth5_min_usd > self.depth10_min_usd:
            issues.append("DEPTH5_MIN_USD > DEPTH10_MIN_USD — проверь пороги.")
        return issues


settings = Settings()
