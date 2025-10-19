# app/config/constants.py
"""
Global constants for the Trade Bot.

This module intentionally contains simple, import-time constants only.
Runtime tunables should be changed via /api/strategy/params or environment.
"""
from app.config.settings import settings

# ───────────────────────────── WS (public data) ─────────────────────────────
# Endpoint resolved from active provider/mode (can be overridden by WS_BASE_URL_RESOLVED)
WS_PUBLIC_ENDPOINT = settings.ws_base_url_resolved

# Protobuf channels (MEXC Spot v3)
WS_CHANNELS = {
    "BOOK_TICKER": "spot@public.aggre.bookTicker.v3.api.pb",  # best bid/ask
    "DEALS":       "spot@public.aggre.deals.v3.api.pb",       # trades stream
    "DEPTH_LIMIT": "spot@public.limit.depth.v3.api.pb",       # top-N depth
}

# Topic update cadence suffix (env: WS_RATE_SUFFIX, default @500ms per stability guidance)
WS_RATE_SUFFIX = settings.ws_rate_suffix or ""  # e.g. "@500ms"; empty → provider default

# Hygiene / limits (all come from env via settings with sensible defaults)
WS_MAX_TOPICS        = settings.ws_max_topics
WS_PING_INTERVAL_SEC = settings.ws_ping_interval_sec
WS_MAX_LIFETIME_SEC  = settings.ws_max_lifetime_sec

# SUBSCRIBE throttle (topics per second) — used by ws client to avoid blocks
WS_SUBSCRIBE_RATE_LIMIT_PER_SEC = settings.ws_subscribe_rate_limit_per_sec

# ───────────────────────────── Strategy defaults ─────────────────────────────
# These are import-time defaults; dynamic runtime values should come from
# PUT /api/strategy/params or from settings.* where applicable.

# Entry requires at least this spread (bps). You can still override at runtime.
MIN_SPREAD_BPS = 3

# Minimum net edge after fees (bps)
EDGE_FLOOR_BPS = 2

# Window around mid (bps) used for absorption/depth checks
ABSORPTION_X_BPS = 10

# Trading constraints (paper defaults)
MAX_CONCURRENT_SYMBOLS = 6         # max symbols the strategy will run in parallel
ORDER_SIZE_USD = 50                # paper notional per order
TIMEOUT_EXIT_SEC = 25              # force exit if held longer than this
