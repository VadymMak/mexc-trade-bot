# app/config/constants.py
"""
Global constants for the Trade Bot.

This module intentionally contains simple, import-time constants only.
Runtime tunables should be changed via /api/strategy/params.
"""

# ───────────────────────────── WS (public data) ─────────────────────────────
# MEXC Protobuf Spot v3
WS_PUBLIC_ENDPOINT = "wss://wbs-api.mexc.com/ws"

# Protobuf channels
WS_CHANNELS = {
    "BOOK_TICKER": "spot@public.aggre.bookTicker.v3.api.pb",  # best bid/ask
    "DEALS":       "spot@public.aggre.deals.v3.api.pb",       # trades stream
    "DEPTH_LIMIT": "spot@public.limit.depth.v3.api.pb",       # top-N depth
}

# Update cadence suffix for topics (account-permissions may restrict lower cadences)
WS_RATE_SUFFIX = "@100ms"  # alternatives: "@10ms" if allowed

# MEXC limits / hygiene
WS_MAX_TOPICS = 30                 # max topics per connection
WS_PING_INTERVAL_SEC = 20          # heartbeat
WS_MAX_LIFETIME_SEC = 23 * 3600 + 2700  # ~23h45m to stay under 24h hard limit

# ───────────────────────────── Strategy defaults ─────────────────────────────
# NB: These are *defaults*. You can override them at runtime via:
#     PUT /api/strategy/params  (see Swagger)

# Entry requires at least this spread (in basis points; 1 bps = 0.01%)
# Was 10; relaxed to 3 so entries can trigger on tighter markets you observed.
MIN_SPREAD_BPS = 3

# Minimum net edge after fees (bps). Paper has no fees; live should subtract maker fee.
# Was 4; relaxed to 2 to allow entries with moderate edge.
EDGE_FLOOR_BPS = 2

# Window around mid (bps) used for absorption/depth checks
ABSORPTION_X_BPS = 10

# Trading constraints
MAX_CONCURRENT_SYMBOLS = 6   # max symbols the strategy will run in parallel
ORDER_SIZE_USD = 50          # paper notional per order
TIMEOUT_EXIT_SEC = 25        # force exit if held longer than this
