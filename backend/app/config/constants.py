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

# Entry filters
MIN_SPREAD_BPS = 3                  # Minimum spread to enter
EDGE_FLOOR_BPS = 2                  # Minimum net edge after fees
ABSORPTION_X_BPS = 10               # Depth check window around mid

# Trading constraints
MAX_CONCURRENT_SYMBOLS = 10         # Max symbols in parallel
ORDER_SIZE_USD = 50                 # Notional per order

# Exit management
TIMEOUT_EXIT_SEC = 30               # Force exit after this time
TAKE_PROFIT_BPS = 2.0               # Initial TP target
STOP_LOSS_BPS = -3.0                # Base for dynamic SL
MIN_HOLD_MS = 600                   # Minimum hold before exit

# ═══════════════════════════════════════════════════════════════════════════
# TRAILING STOP SETTINGS (NEW)
# ═══════════════════════════════════════════════════════════════════════════
ENABLE_TRAILING_STOP = True         # Enable/disable trailing stop
TRAILING_ACTIVATION_BPS = 2.0       # Activate trailing at +X bps profit
TRAILING_STOP_BPS = 0.8             # Trail X bps behind peak
TRAILING_STEP_BPS = 0.2             # Update only if peak moves +X bps
# ═══════════════════════════════════════════════════════════════════════════

