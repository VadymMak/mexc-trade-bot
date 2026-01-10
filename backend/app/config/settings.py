# app/config/settings.py
import os
from typing import List, Any, Tuple, Optional
from pathlib import Path
from dotenv import load_dotenv

# ═══ ЗАГРУЗКА .env ФАЙЛА ═══
# Найти .env относительно этого файла (backend/.env)
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path, override=False)
print(f"🔧 Settings: loaded .env from {env_path} (exists: {env_path.exists()})")

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, AliasChoices


def _normalize_cors(v: Any) -> list[str]:
    """
    Accepts list/tuple, CSV string, or JSON-ish list string.
    Returns a clean list; preserves a single '*' if provided.
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
        # strip brackets for JSON-ish strings
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        return [p.strip() for p in s.split(",") if p.strip()]
    s = str(v).strip()
    return [s] if s else []


def _coalesce_symbol(raw: str) -> str:
    """
    Normalize symbols like ' BTC USDT ' → 'BTCUSDT', 'eth/usdt' → 'ETHUSDT'.
    Removes spaces and separators, then uppercases.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    for sep in (" ", "/", "-", "_"):
        s = s.replace(sep, "")
    return s.upper()


def _csv_split(s: str | None) -> list[str]:
    """
    Split CSV or JSON-ish list string to a clean list of symbols.
    Trims whitespace and ignores empties.
    """
    if not s:
        return []
    ss = s.strip()
    if ss.startswith("[") and ss.endswith("]"):
        ss = ss[1:-1]
    parts = [x.strip() for x in ss.split(",") if x.strip()]
    return parts


def _unique_preserve(seq: list[str]) -> list[str]:
    """De-duplicate while preserving order."""
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


class Settings(BaseSettings):
    """
    Unified settings:
    - Provider/mode resolution
    - WS/REST tunables for resiliency (rate suffix, subscribe throttle, lifecycle, timeouts, retries)
    - Scanner & strategy thresholds
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

    gate_maker_fee: float | None = Field(
        default=(lambda v=os.getenv("GATE_MAKER_FEE"): float(v) if v not in (None, "") else None)(),
        validation_alias=AliasChoices("GATE_MAKER_FEE", "gate_maker_fee"),
        description="Optional hard override for Gate maker fee (fraction, e.g. 0.0002)",
    )

    gate_taker_fee: float | None = Field(
        default=(lambda v=os.getenv("GATE_TAKER_FEE"): float(v) if v not in (None, "") else None)(),
        validation_alias=AliasChoices("GATE_TAKER_FEE", "gate_taker_fee"),
        description="Optional hard override for Gate taker fee (fraction, e.g. 0.0006)",
    )

    gate_zero_fee: bool | None = Field(
        default=(os.getenv("GATE_ZERO_FEE", "").lower() in {"1","true","yes","on"}) if os.getenv("GATE_ZERO_FEE") else None,
        validation_alias=AliasChoices("GATE_ZERO_FEE", "gate_zero_fee"),
        description="Optional hard override to force 'zero_fee' flag",
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
        default=os.getenv("GATE_TESTNET_REST_BASE", "https://api.gateio.ws/api/v4"),  
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

    # ========== HTTP/WS core (legacy MEXC names kept for compat) ==========
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

    # ---------- NEW: Global REST tuning (used by exchange HTTP clients) ----------
    rest_timeout_sec: float = Field(
        default=float(os.getenv("REST_TIMEOUT_SEC", "10.0")),
        validation_alias=AliasChoices("REST_TIMEOUT_SEC", "rest_timeout_sec"),
        description="Per-request timeout for exchange REST calls.",
    )
    rest_retry_attempts: int = Field(
        default=int(os.getenv("REST_RETRY_ATTEMPTS", "2")),
        validation_alias=AliasChoices("REST_RETRY_ATTEMPTS", "rest_retry_attempts"),
        description="How many times to retry on timeouts/5xx/429 (not counting the first try).",
    )
    rest_retry_backoff_ms: int = Field(
        default=int(os.getenv("REST_RETRY_BACKOFF_MS", "1500")),
        validation_alias=AliasChoices("REST_RETRY_BACKOFF_MS", "rest_retry_backoff_ms"),
        description="Initial backoff between REST retries (milliseconds).",
    )
    rest_retry_backoff_factor: float = Field(
        default=float(os.getenv("REST_RETRY_BACKOFF_FACTOR", "2.0")),
        validation_alias=AliasChoices("REST_RETRY_BACKOFF_FACTOR", "rest_retry_backoff_factor"),
        description="Multiplier for exponential backoff: next = prev * factor.",
    )
    rest_backoff_max_sec: float = Field(
        default=float(os.getenv("REST_BACKOFF_MAX_SEC", "60.0")),
        validation_alias=AliasChoices("REST_BACKOFF_MAX_SEC", "rest_backoff_max_sec"),
        description="Maximum backoff delay between retries (seconds)",
    )

    # ========== HTTP Polling Client Settings ==========
    http_poll_interval_sec: float = Field(
        default=float(os.getenv("HTTP_POLL_INTERVAL_SEC", "1.0")),
        validation_alias=AliasChoices("HTTP_POLL_INTERVAL_SEC", "http_poll_interval_sec"),
        description="Interval between HTTP polls for market data (seconds)",
    )
    http_unavailable_threshold: int = Field(
        default=int(os.getenv("HTTP_UNAVAILABLE_THRESHOLD", "5")),
        validation_alias=AliasChoices("HTTP_UNAVAILABLE_THRESHOLD", "http_unavailable_threshold"),
        description="Number of consecutive failures before marking symbol unavailable",
    )
    http_max_concurrent_requests: int = Field(
        default=int(os.getenv("HTTP_MAX_CONCURRENT_REQUESTS", "10")),
        validation_alias=AliasChoices("HTTP_MAX_CONCURRENT_REQUESTS", "http_max_concurrent_requests"),
        description="Max concurrent HTTP requests (semaphore limit)",
    )
    http_debug_logging: bool = Field(
        default=os.getenv("HTTP_DEBUG_LOGGING", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("HTTP_DEBUG_LOGGING", "http_debug_logging"),
        description="Enable verbose logging for HTTP client",
    )

    # ========== MEXC Fees (defaults, can be overridden) ==========
    mexc_maker_fee: float = Field(
        default=0.0,
        validation_alias=AliasChoices("MEXC_MAKER_FEE", "mexc_maker_fee"),
        description="MEXC maker fee (default 0% = competitive advantage)",
    )
    mexc_taker_fee: float = Field(
        default=0.0005,
        validation_alias=AliasChoices("MEXC_TAKER_FEE", "mexc_taker_fee"),
        description="MEXC taker fee (default 0.05% = 5 bps)",
    )
    mexc_zero_fee: bool = Field(
        default=True,
        validation_alias=AliasChoices("MEXC_ZERO_FEE", "mexc_zero_fee"),
        description="MEXC maker fee is exactly 0.0 (promotional feature)",
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
    
    http_debug_gate: bool = Field(
        default=os.getenv("HTTP_DEBUG_GATE", "0").lower() in {"1","true","yes","on"},
        validation_alias=AliasChoices("HTTP_DEBUG_GATE", "http_debug_gate"),
        description="Verbose httpx logs for Gate REST"
    )
    http_debug_mexc: bool = Field(
        default=os.getenv("HTTP_DEBUG_MEXC", "0").lower() in {"1","true","yes","on"},
        validation_alias=AliasChoices("HTTP_DEBUG_MEXC", "http_debug_mexc"),
        description="Verbose httpx logs for MEXC REST"
    )
    scan_endpoint_timeout_sec: int = Field(
        default=int(os.getenv("SCAN_ENDPOINT_TIMEOUT_SEC", "18")),  # было 12, стало 18
        validation_alias=AliasChoices("SCAN_ENDPOINT_TIMEOUT_SEC", "scan_endpoint_timeout_sec"),
        description="Hard cap for /api/scanner/* route execution (accounts for retries)"
    )
    # ========== Scanner Concurrency Controls ==========
    gate_scan_concurrency: int = Field(
        default=int(os.getenv("GATE_SCAN_CONCURRENCY", "12")),
        validation_alias=AliasChoices("GATE_SCAN_CONCURRENCY", "gate_scan_concurrency"),
        description="Max parallel enrichments for Gate scanner (Stage 2)",
    )
    mexc_scan_concurrency: int = Field(
        default=int(os.getenv("MEXC_SCAN_CONCURRENCY", "10")),
        validation_alias=AliasChoices("MEXC_SCAN_CONCURRENCY", "mexc_scan_concurrency"),
        description="Max parallel enrichments for MEXC scanner (Stage 2)",
    )

    # ========== Scanner Features / Weights / TTL ==========
    feature_vol_pattern_v1: bool = Field(default=os.getenv("FEATURE_VOL_PATTERN_V1", "0").lower() in {"1", "true", "yes", "on"},
                                         validation_alias=AliasChoices("FEATURE_VOL_PATTERN_V1", "feature_vol_pattern_v1"))
    feature_glass_bands: bool = Field(default=os.getenv("FEATURE_GLASS_BANDS", "0").lower() in {"1", "true", "yes", "on"},
                                      validation_alias=AliasChoices("FEATURE_GLASS_BANDS", "feature_glass_bands"))
    feature_time_aware: bool = Field(default=os.getenv("FEATURE_TIME_AWARE", "0").lower() in {"1", "true", "yes", "on"},
                                     validation_alias=AliasChoices("FEATURE_TIME_AWARE", "feature_time_aware"))
    exec_dry_run: bool = Field(default=os.getenv("EXEC_DRY_RUN", "1").lower() in {"1", "true", "yes", "on"},
                               validation_alias=AliasChoices("EXEC_DRY_RUN", "exec_dry_run"))

    # legacy scoring knobs
    sc_w_spread_bps: float = Field(default=float(os.getenv("SC_W_SPREAD_BPS", "15")))
    sc_w_sum5: float = Field(default=float(os.getenv("SC_W_SUM5", "18")))
    sc_w_ratio: float = Field(default=float(os.getenv("SC_W_RATIO", "18")))
    sc_w_volpat: float = Field(default=float(os.getenv("SC_W_VOLPAT", "18")))
    sc_w_stability: float = Field(default=float(os.getenv("SC_W_STABILITY", "18")))
    sc_w_spoof: float = Field(default=float(os.getenv("SC_W_SPOOF", "8")))
    sc_w_exit: float = Field(default=float(os.getenv("SC_W_EXIT", "8")))

    # new weights for market_scanner._score_row
    score_w_usd_per_min: float = Field(default=float(os.getenv("SCORE_W_USD_PER_MIN", "1.0")))
    score_w_depth: float = Field(default=float(os.getenv("SCORE_W_DEPTH", "0.7")))
    score_w_spread: float = Field(default=float(os.getenv("SCORE_W_SPREAD", "0.5")))
    score_w_eff: float = Field(default=float(os.getenv("SCORE_W_EFF", "0.6")))
    score_w_vol_pattern: float = Field(default=float(os.getenv("SCORE_W_VOL_PATTERN", "0.4")))
    score_w_dca: float = Field(default=float(os.getenv("SCORE_W_DCA", "0.5")))
    score_w_atr: float = Field(default=float(os.getenv("SCORE_W_ATR", "0.3")))

    scanner_cache_ttl: float = Field(default=float(os.getenv("SCANNER_CACHE_TTL", "20.0")))

    # ======== Strategy thresholds (prompt) ========
    usdpm_min: float = Field(default=float(os.getenv("USDPM_MIN", "20.0")))
    tpm_min: int = Field(default=int(os.getenv("TPM_MIN", "5")))
    median_usd_min: float = Field(default=float(os.getenv("MEDIAN_USD_MIN", "0")))
    atr_max_usd: float = Field(default=float(os.getenv("ATR_MAX_USD", "8.0")))
    atr_pct_max_1m: float = Field(default=float(os.getenv("ATR_PCT_MAX_1M", "0.8")))
    spread_bps_min: int = Field(default=int(os.getenv("SPREAD_BPS_MIN", "1")))
    spread_bps_max: int = Field(default=int(os.getenv("SPREAD_BPS_MAX", "7")))
    depth5_min_usd: int = Field(default=int(os.getenv("DEPTH5_MIN_USD", "1000")))
    depth10_min_usd: int = Field(default=int(os.getenv("DEPTH10_MIN_USD", "3000")))
    usdpm_per_depth5_min_ratio: float = Field(default=float(os.getenv("USDPM_PER_DEPTH5_MIN_RATIO", "0.1")))
    stability_min: int = Field(default=int(os.getenv("STABILITY_MIN", "70")))
    vol_pattern_min: int = Field(default=int(os.getenv("VOL_PATTERN_MIN", "0")))
    vol_pattern_max: int = Field(default=int(os.getenv("VOL_PATTERN_MAX", "100")))

    # ======== Exec limits / risk ========
    order_size_usd_min: float = Field(default=float(os.getenv("ORDER_SIZE_USD_MIN", "1.0")))
    order_size_usd_max: float = Field(default=float(os.getenv("ORDER_SIZE_USD_MAX", "2.0")))
    exec_cancel_timeout_sec: int = Field(default=int(os.getenv("EXEC_CANCEL_TIMEOUT_SEC", "10")))
    exec_tp_pct: float = Field(default=float(os.getenv("EXEC_TP_PCT", "0.1")))
    max_exposure_usd: float = Field(default=float(os.getenv("MAX_EXPOSURE_USD", "1000")))
    # ════════════════════════════════════════════════════════════════
    # POSITION SIZING & SMART EXECUTOR (Phase 2)
    # ════════════════════════════════════════════════════════════════
    
    # Target position size (базовый размер входа)
    TARGET_POSITION_SIZE_USD: float = Field(
        default=float(os.getenv("TARGET_POSITION_SIZE_USD", "50.0")),
        validation_alias=AliasChoices("TARGET_POSITION_SIZE_USD", "target_position_size_usd"),
        description="Target position size per trade (USD). Recommended: $50-200"
    )
    
    # Maximum position per symbol
    MAX_PER_SYMBOL_USD: float = Field(
        default=float(os.getenv("MAX_PER_SYMBOL_USD", "300.0")),
        validation_alias=AliasChoices("MAX_PER_SYMBOL_USD", "max_per_symbol_usd"),
        description="Maximum total position per symbol (USD)"
    )
    
    # Smart Executor - Order splitting
    SMART_EXECUTOR_ENABLED: bool = Field(
        default=os.getenv("SMART_EXECUTOR_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("SMART_EXECUTOR_ENABLED", "smart_executor_enabled"),
        description="Enable smart order execution with splitting"
    )
    
    SPLIT_LARGE_ORDERS: bool = Field(
        default=os.getenv("SPLIT_LARGE_ORDERS", "true").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("SPLIT_LARGE_ORDERS", "split_large_orders"),
        description="Automatically split orders larger than MM capacity"
    )
    
    MAX_ORDER_SIZE_USD: float = Field(
        default=float(os.getenv("MAX_ORDER_SIZE_USD", "50.0")),
        validation_alias=AliasChoices("MAX_ORDER_SIZE_USD", "max_order_size_usd"),
        description="Maximum size of single order before splitting (USD)"
    )
    
    MIN_SPLIT_DELAY_SEC: float = Field(
        default=float(os.getenv("MIN_SPLIT_DELAY_SEC", "0.8")),
        validation_alias=AliasChoices("MIN_SPLIT_DELAY_SEC", "min_split_delay_sec"),
        description="Minimum delay between split orders (seconds)"
    )
    
    MAX_SPLIT_DELAY_SEC: float = Field(
        default=float(os.getenv("MAX_SPLIT_DELAY_SEC", "1.2")),
        validation_alias=AliasChoices("MAX_SPLIT_DELAY_SEC", "max_split_delay_sec"),
        description="Maximum delay between split orders (seconds)"
    )
    
    # MM Detection integration
    MM_DETECTION_ENABLED: bool = Field(
        default=os.getenv("MM_DETECTION_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("MM_DETECTION_ENABLED", "mm_detection_enabled"),
        description="Enable Market Maker detection for adaptive sizing"
    )
    
    MM_MIN_CONFIDENCE: float = Field(
        default=float(os.getenv("MM_MIN_CONFIDENCE", "0.7")),
        validation_alias=AliasChoices("MM_MIN_CONFIDENCE", "mm_min_confidence"),
        description="Minimum MM confidence to use adaptive sizing (0.0-1.0)"
    )
    
    AVOID_MM_DEPARTURE: bool = Field(
        default=os.getenv("AVOID_MM_DEPARTURE", "true").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("AVOID_MM_DEPARTURE", "avoid_mm_departure"),
        description="Avoid scaring away Market Maker with large orders"
    )
    
    # Position Sizing Mode
    POSITION_SIZING_MODE: str = Field(
        default=os.getenv("POSITION_SIZING_MODE", "conservative").lower(),
        validation_alias=AliasChoices("POSITION_SIZING_MODE", "position_sizing_mode"),
        description="Sizing strategy: 'conservative', 'balanced', or 'aggressive'"
    )
    # ======== Trading Schedule (Time Windows) ========
    trading_schedule_enabled: bool = Field(
        default=os.getenv("TRADING_SCHEDULE_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("TRADING_SCHEDULE_ENABLED", "trading_schedule_enabled"),
        description="Enable trading time windows (only trade during specified hours)"
    )
    
    trading_start_time: str = Field(
        default=os.getenv("TRADING_START_TIME", "10:00"),
        validation_alias=AliasChoices("TRADING_START_TIME", "trading_start_time"),
        description="Trading window start time (HH:MM format, 24-hour, local timezone)"
    )
    
    trading_end_time: str = Field(
        default=os.getenv("TRADING_END_TIME", "20:00"),
        validation_alias=AliasChoices("TRADING_END_TIME", "trading_end_time"),
        description="Trading window end time (HH:MM format, 24-hour, local timezone)"
    )
    
    trading_timezone: str = Field(
        default=os.getenv("TRADING_TIMEZONE", "Europe/Istanbul"),
        validation_alias=AliasChoices("TRADING_TIMEZONE", "trading_timezone"),
        description="Timezone for trading schedule (e.g., Europe/Istanbul, America/New_York)"
    )
    
    trade_on_weekends: bool = Field(
        default=os.getenv("TRADE_ON_WEEKENDS", "true").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("TRADE_ON_WEEKENDS", "trade_on_weekends"),
        description="Allow trading on Saturday and Sunday"
    )
    
    close_before_end_minutes: int = Field(
        default=int(os.getenv("CLOSE_BEFORE_END_MINUTES", "10")),
        validation_alias=AliasChoices("CLOSE_BEFORE_END_MINUTES", "close_before_end_minutes"),
        description="Close all positions X minutes before end time (prevents holding overnight)"
    )
    # ======== Trailing Stop Settings ========
    trailing_stop_enabled: bool = Field(
        default=os.getenv("TRAILING_STOP_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("TRAILING_STOP_ENABLED", "trailing_stop_enabled"),
        description="Enable trailing stop (move SL to lock in profits as price moves favorably)"
    )
    
    trailing_activation_bps: float = Field(
        default=float(os.getenv("TRAILING_ACTIVATION_BPS", "1.5")),
        validation_alias=AliasChoices("TRAILING_ACTIVATION_BPS", "trailing_activation_bps"),
        description="Profit threshold (in bps) to activate trailing stop"
    )
    
    trailing_distance_bps: float = Field(
        default=float(os.getenv("TRAILING_DISTANCE_BPS", "0.5")),
        validation_alias=AliasChoices("TRAILING_DISTANCE_BPS", "trailing_distance_bps"),
        description="Distance from peak (in bps) to trail the stop loss"
    )
    idempotency_window_sec: int = 300
    idempotency_max_size: int = 10000  # ← This is probably missing or named differently
    max_watchlist_bulk: int = 50

    # =============================================================================
    # Idempotency
    # =============================================================================
    idempotency_enabled: bool = Field(
        default=os.getenv("IDEMPOTENCY_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("IDEMPOTENCY_ENABLED", "idempotency_enabled"),
        description="Enable idempotency checks for mutation endpoints (place/flatten/close_all)"
    )

    idempotency_ttl_seconds: int = Field(
        default=int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "600")),
        ge=60,        # minimum 1 minute
        le=3600,      # maximum 1 hour
        validation_alias=AliasChoices("IDEMPOTENCY_TTL_SECONDS", "idempotency_ttl_seconds"),
        description="How long to cache idempotency keys (seconds). Default: 10 minutes."
    )

    idempotency_backend: str = Field(
        default=os.getenv("IDEMPOTENCY_BACKEND", "memory").lower(),
        validation_alias=AliasChoices("IDEMPOTENCY_BACKEND", "idempotency_backend"),
        description="Storage backend: 'memory' (in-process dict) or 'redis' (external cache)"
    )

    idempotency_redis_url: str | None = Field(
        default=os.getenv("IDEMPOTENCY_REDIS_URL"),
        validation_alias=AliasChoices("IDEMPOTENCY_REDIS_URL", "idempotency_redis_url"),
        description="Redis URL for idempotency cache (required if backend=redis). Example: redis://localhost:6379/1"
    )

    # ======== Inclusion / Exclusions ========
    include_stables: bool = Field(default=os.getenv("INCLUDE_STABLES", "false").lower() in {"1", "true", "yes", "on"})
    exclude_leveraged: bool = Field(default=os.getenv("EXCLUDE_LEVERAGED", "true").lower() in {"1", "true", "yes", "on"})
    exclude_perps: bool = Field(default=os.getenv("EXCLUDE_PERPS", "true").lower() in {"1", "true", "yes", "on"})

    # ========================================
    # ML Data Collection Settings
    # ========================================
    ML_LOGGING_ENABLED: bool = Field(
        default=os.getenv("ML_LOGGING_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        validation_alias=AliasChoices("ML_LOGGING_ENABLED", "ml_logging_enabled"),
        description="Enable ML data collection (snapshots for training)"
    )
    ML_LOGGING_SYMBOLS: str = Field(
        default=os.getenv("ML_LOGGING_SYMBOLS", ""),
        validation_alias=AliasChoices("ML_LOGGING_SYMBOLS", "ml_logging_symbols"),
        description="Comma-separated symbols to log for ML (e.g., BTCUSDT,ETHUSDT)"
    )
    ML_LOGGING_INTERVAL_SEC: float = Field(
        default=float(os.getenv("ML_LOGGING_INTERVAL_SEC", "2.0")),
        validation_alias=AliasChoices("ML_LOGGING_INTERVAL_SEC", "ml_logging_interval_sec"),
        description="Snapshot interval in seconds (default: 2.0)"
    )
    ML_LOGGING_PRESET: str = Field(
        default=os.getenv("ML_LOGGING_PRESET", "hedgehog"),
        validation_alias=AliasChoices("ML_LOGGING_PRESET", "ml_logging_preset"),
        description="Scanner preset for ML metrics (hedgehog/balanced/etc.)"
    )

    # ======== SSE / WS tuning ========
    sse_ping_interval_ms: int = Field(default=int(os.getenv("SSE_PING_INTERVAL_MS", "15000")))
    sse_retry_base_ms: int = Field(default=int(os.getenv("SSE_RETRY_BASE_MS", "1000")))
    sse_retry_max_ms: int = Field(default=int(os.getenv("SSE_RETRY_MAX_MS", "20000")))
    ws_orderbook_snapshot_levels: int = Field(default=int(os.getenv("WS_OB_SNAPSHOT_LEVELS", "10")))
    ws_orderbook_delta_buffer: int = Field(default=int(os.getenv("WS_OB_DELTA_BUFFER", "64")))

    # ---------- NEW: WS lifecycle & rate controls (used by ws_client/constants) ----------
    ws_rate_suffix: str = Field(
        default=os.getenv("WS_RATE_SUFFIX", "@100ms"),
        validation_alias=AliasChoices("WS_RATE_SUFFIX", "ws_rate_suffix"),
        description="Requested WS frequency suffix, e.g. '@100ms'. Empty string means provider default.",
    )
    ws_subscribe_rate_limit_per_sec: int = Field(
        default=int(os.getenv("WS_SUBSCRIBE_RATE_LIMIT_PER_SEC", "8")),
        validation_alias=AliasChoices("WS_SUBSCRIBE_RATE_LIMIT_PER_SEC", "ws_subscribe_rate_limit_per_sec"),
        description="Throttle for SUBSCRIPTION sends (topics per second).",
    )
    ws_max_topics: int = Field(
        default=int(os.getenv("WS_MAX_TOPICS", "30")),
        validation_alias=AliasChoices("WS_MAX_TOPICS", "ws_max_topics"),
        description="Max topics per single WS connection before sharding.",
    )
    ws_ping_interval_sec: int = Field(
        default=int(os.getenv("WS_PING_INTERVAL_SEC", "20")),
        validation_alias=AliasChoices("WS_PING_INTERVAL_SEC", "ws_ping_interval_sec"),
        description="Send JSON PING if no frames for this many seconds.",
    )
    ws_ping_timeout: float = Field(
    default=float(os.getenv("WS_PING_TIMEOUT", "10.0")),
    validation_alias=AliasChoices("WS_PING_TIMEOUT", "ws_ping_timeout"),
    description="WebSocket ping timeout (seconds, float for Gate compatibility)"
    )

    ws_recv_timeout_multiplier: float = Field(
        default=float(os.getenv("WS_RECV_TIMEOUT_MULTIPLIER", "2.5")),
        validation_alias=AliasChoices("WS_RECV_TIMEOUT_MULTIPLIER", "ws_recv_timeout_multiplier"),
        ge=1.5,
        le=5.0,
        description="Multiplier for recv timeout (ping_interval * multiplier)"
    )

    gate_depth_limit: int = Field(
        default=int(os.getenv("GATE_DEPTH_LIMIT", "10")),
        validation_alias=AliasChoices("GATE_DEPTH_LIMIT", "gate_depth_limit"),
        ge=5,
        le=50,
        description="Order book depth limit for Gate WS"
    )
    ws_max_lifetime_sec: int = Field(
        default=int(os.getenv("WS_MAX_LIFETIME_SEC", str(23 * 3600))),
        validation_alias=AliasChoices("WS_MAX_LIFETIME_SEC", "ws_max_lifetime_sec"),
        description="Force WS reconnect before 24h limit; avoids midnight edge cases and exchange-side stale connections"
    )

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
        return [_coalesce_symbol(s) for s in _csv_split(self.symbols_csv)]

    @property
    def symbols_unique(self) -> List[str]:
        return _unique_preserve(self.symbols)

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
        if self.is_gate:
            # ✅ ALWAYS use production for public endpoints (faster, more reliable)
            return self.gate_rest_base
        if self.is_binance:
            return self.binance_rest_base
        return self.mexc_rest_base or self.rest_base_url

    @property
    def ws_base_url_resolved(self) -> str:
        if self.ws_base_url_override:
            return self.ws_base_url_override
        if self.is_gate:
            env = (self.gate_ws_env or "").strip().upper()
            if env in {"TESTNET", "SANDBOX"}:
                return self.gate_testnet_ws_base
            if env == "LIVE":
                return self.gate_ws_base
            return self.gate_testnet_ws_base if self.is_demo else self.gate_ws_base
        if self.is_binance:
            return self.binance_ws_base
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

    # ---------- UI/Router helpers ----------
    @property
    def available_providers(self) -> list[str]:
        return ["gate", "mexc", "binance"]

    def provider_state(self) -> dict:
        return {
            "active": self.active_provider.lower(),
            "mode": self.active_mode,
            "available": self.available_providers,
            "ws_enabled": bool(self.enable_ws),
            "revision": None,
        }

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
        if self.symbols and any(not s.endswith(self.quote) for s in self.symbols):
            issues.append(f"В SYMBOLS есть пары без квоты {self.quote} (ожидается окончание тикера на {self.quote}).")
        if self.depth5_min_usd > self.depth10_min_usd:
            issues.append("DEPTH5_MIN_USD > DEPTH10_MIN_USD — проверь пороги.")
        return issues

    # ═══════════════════════════════════════════════════════════════════
    # ML SETTINGS
    # ═══════════════════════════════════════════════════════════════════
    ML_ENABLED: bool = False
    ML_MODEL_PATH: str = "ml_models/mexc_ml_v1.json"
    ML_MIN_CONFIDENCE: float = 0.6
    ML_WEIGHT: float = 0.2
    ML_USE_FILTER: bool = True
    ML_USE_WEIGHT: bool = False

settings = Settings()
