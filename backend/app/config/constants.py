# app/config/constants.py
"""
Global constants for MEXC Trade Bot.
"""

# ───────────────────────────── WS settings ─────────────────────────────
# Public WS endpoint (Protobuf Spot v3)
WS_PUBLIC_ENDPOINT = "wss://wbs-api.mexc.com/ws"

# Public data channels (protobuf)
WS_CHANNELS = {
    "BOOK_TICKER": "spot@public.aggre.bookTicker.v3.api.pb",   # best bid/ask
    "DEALS": "spot@public.aggre.deals.v3.api.pb",              # trades stream
    "DEPTH_LIMIT": "spot@public.limit.depth.v3.api.pb",        # L10 depth (optional)
}

# Default update cadence
WS_RATE_SUFFIX = "@100ms"  # alternatives: "@10ms" (if account allows)

# MEXC limit: max topics per single connection
WS_MAX_TOPICS = 30

# Heartbeat / reconnect
WS_PING_INTERVAL_SEC = 20
# Force reconnect a bit under 24h (MEXC limit)
WS_MAX_LIFETIME_SEC = 23 * 3600 + 2700  # ~23h45m

# ───────────────────────────── Strategy (paper/live) ─────────────────────────────
# Minimum spread threshold in bps (0.01% units)
MIN_SPREAD_BPS = 10

# Minimum edge after fees (bps). Paper ignores fees; live should subtract maker fee.
EDGE_FLOOR_BPS = 4

# Bps window around mid used for absorption checks
ABSORPTION_X_BPS = 10

# Trading constraints
MAX_CONCURRENT_SYMBOLS = 6   # max active symbols per session
ORDER_SIZE_USD = 50          # default order notional (paper)
TIMEOUT_EXIT_SEC = 25        # max holding time before forced exit
