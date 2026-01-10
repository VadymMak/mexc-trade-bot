# app/infra/metrics.py
from __future__ import annotations
import time
from prometheus_client import Counter, Histogram, Gauge

# ───────────────────────── WS metrics ─────────────────────────
# NOTE: type ∈ {"book_ticker","deals","depth"} (depth optional)
ticks_total = Counter(
    "ws_ticks_total",
    "Total parsed WS updates by type",
    ["symbol", "type"],  # ← added "type" to match ws_client usage
)

ws_lag_seconds = Histogram(
    "ws_lag_seconds",
    "Book/deals lag (receive_now - send_time), seconds",
    ["symbol"],
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5, 10),
)

ws_reconnects_total = Counter(
    "ws_reconnects_total", "Total WebSocket reconnects"
)

ws_active_subscriptions = Gauge(
    "ws_active_subscriptions", "Active WS subscriptions topics count"
)

# ▶︎ Optional gauges a scheduler can set (derived rates also possible in PromQL)
ws_ticks_per_sec = Gauge(
    "ws_ticks_per_sec", "Observed ticks per second over the last sampling window"
)

ws_depth_updates_total = Counter(
    "ws_depth_updates_total", "Total L2 orderbook (depth) updates", ["symbol"]
)

ws_depth_updates_per_sec = Gauge(
    "ws_depth_updates_per_sec", "Observed depth updates per second over the last sampling window"
)

# Quick status surface for UI /healthz
ws_lag_ms = Gauge(
    "ws_lag_ms", "Latest observed WS lag for any symbol, milliseconds"
)

# ───────────────────────── Scanner / Tape / Glass ─────────────────────────
# Stage-2 enrich timing
scanner_enrich_seconds = Histogram(
    "scanner_enrich_seconds",
    "Stage-2 enrich processing time per symbol, seconds",
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2),
)

# Cache performance
scanner_cache_hitrate = Gauge(
    "scanner_cache_hitrate", "Scanner cache hit ratio (0..1)"
)

# Number of candidates that survived filters (after Stage-2)
scanner_candidates = Gauge(
    "scanner_candidates", "Number of surviving candidates after filters"
)

# Ratio-gate hits (usdpm < 0.1 * depth5) — to track how often we filter noise
tape_ratio_hits_total = Counter(
    "tape_ratio_hits_total", "Times ratio-gate filtered a row (usdpm/depth5)", ["symbol"]
)

# ───────────────────── API (/scan, /top, etc.) ─────────────────────
api_scan_requests_total = Counter(
    "api_scan_requests_total", "Number of /scan requests served"
)

api_scan_latency_seconds = Histogram(
    "api_scan_latency_seconds",
    "Latency of /scan handler, seconds",
    buckets=(0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5),
)

api_top_requests_total = Counter(
    "api_top_requests_total", "Number of /top or /top_tiered requests served"
)

api_top_latency_seconds = Histogram(
    "api_top_latency_seconds",
    "Latency of /top or /top_tiered handler, seconds",
    buckets=(0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5),
)

# ───────────────────── Strategy counters & gauges ─────────────────────
strategy_entries_total = Counter(
    "strategy_entries_total",
    "Number of strategy entries (BUY) placed",
    ["symbol"],
)

strategy_exits_total = Counter(
    "strategy_exits_total",
    "Number of strategy exits (SELL) placed, grouped by reason",
    ["symbol", "reason"],  # reason ∈ {TP,SL,TIMEOUT}
)

strategy_open_positions = Gauge(
    "strategy_open_positions",
    "Open position flag per symbol (0/1)",
    ["symbol"],
)

strategy_realized_pnl_total = Gauge(
    "strategy_realized_pnl_total",
    "Total realized PnL (quote currency) per symbol",
    ["symbol"],
)

# Count of currently running per-symbol strategy loops. (low cardinality)
strategy_symbols_running = Gauge(
    "strategy_symbols_running",
    "Number of active strategy symbol loops"
)

# ▶︎ NEW: execution attempt/cancel/fill counts (dry-run or live)
exec_attempts_total = Counter(
    "exec_attempts_total", "Total order attempts (BUY placements)", ["symbol"]
)
exec_cancels_total = Counter(
    "exec_cancels_total", "Total cancels issued", ["symbol", "reason"]  # reason ∈ {timeout,wall_pulled,manual}
)
exec_fills_total = Counter(
    "exec_fills_total", "Total successful fills (BUY then SELL)", ["symbol"]
)

# ───────────────────── Strategy histograms ─────────────────────
# NOTE: Prometheus histograms are non-negative. For PnL in bps (which can be negative),
# record ABSOLUTE magnitude; sign can be inferred elsewhere.
strategy_trade_pnl_bps = Histogram(
    "strategy_trade_pnl_bps",
    "Absolute realized PnL per trade, in basis points (sign not captured here).",
    ["symbol"],
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0, 20.0, 50.0),
)

strategy_trade_duration_seconds = Histogram(
    "strategy_trade_duration_seconds",
    "Trade holding time (entry→exit), seconds.",
    ["symbol"],
    buckets=(0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 10, 20, 30, 60, 120, 300, 600),
)

strategy_edge_bps_at_entry = Histogram(
    "strategy_edge_bps_at_entry",
    "Edge at entry decision time, in basis points.",
    ["symbol"],
    buckets=(0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0),
)

# ▶︎ Optional separate “time in book” (may differ from full trade duration if partials)
strategy_time_in_book_seconds = Histogram(
    "strategy_time_in_book_seconds",
    "Time limit orders stayed on book before cancel/fill, seconds.",
    ["symbol"],
    buckets=(0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 10, 20, 30, 60, 120),
)

# ───────────────────── Process / compatibility helpers ─────────────────────
# Report process uptime (seconds) for /healthz
process_uptime_sec = Gauge("process_uptime_sec", "Process uptime in seconds")

_process_start_ts = time.time()

# ───────────────────── REALISTIC SIMULATION METRICS ─────────────────────
# Metrics for paper trading realistic simulation

# Slippage distribution
simulation_slippage_bps = Histogram(
    "simulation_slippage_bps",
    "Slippage in basis points for simulated orders",
    ["symbol"],
    buckets=(0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0),
)

# Fees accumulated
simulation_fees_total_usd = Counter(
    "simulation_fees_total_usd",
    "Total fees accumulated in USD (paper simulation)",
    ["symbol"],
)

# Order rejections
simulation_rejections_total = Counter(
    "simulation_rejections_total",
    "Total number of rejected orders (paper simulation)",
    ["symbol"],
)

# Partial fills
simulation_partial_fills_total = Counter(
    "simulation_partial_fills_total",
    "Total number of partial fills (paper simulation)",
    ["symbol"],
)

# Partial fill ratio (how much of requested qty was filled)
simulation_partial_fill_ratio = Histogram(
    "simulation_partial_fill_ratio",
    "Ratio of filled qty to requested qty (0.7-1.0)",
    ["symbol"],
    buckets=(0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0),
)

# Latency distribution
simulation_latency_ms = Histogram(
    "simulation_latency_ms",
    "Simulated latency in milliseconds",
    ["symbol"],
    buckets=(25, 50, 75, 100, 125, 150, 175, 200, 250, 300),
)

# Total simulated orders (for calculating rates)
simulation_orders_total = Counter(
    "simulation_orders_total",
    "Total orders processed by simulation (accepted + rejected)",
    ["symbol", "side"],  # side ∈ {BUY, SELL}
)

# Average slippage gauge (for quick dashboard view)
simulation_avg_slippage_bps = Gauge(
    "simulation_avg_slippage_bps",
    "Average slippage in bps (last N orders, updated periodically)",
)

# Rejection rate gauge (for quick dashboard view)
simulation_rejection_rate = Gauge(
    "simulation_rejection_rate",
    "Order rejection rate as fraction (0.0-1.0)",
)

# Partial fill rate gauge (for quick dashboard view)
simulation_partial_fill_rate = Gauge(
    "simulation_partial_fill_rate",
    "Partial fill rate as fraction (0.0-1.0)",
)

# Enable/disable flag (so we can monitor if simulation is active)
simulation_enabled = Gauge(
    "simulation_enabled",
    "Whether realistic simulation is enabled (1=on, 0=off)",
)

def update_uptime_now() -> None:
    """Set the process_uptime_sec gauge to current uptime."""
    try:
        process_uptime_sec.set(max(0.0, time.time() - _process_start_ts))
    except Exception:
        pass

# Compatibility aliases so routers can use shorter names:
# - health expects: ticks_per_sec, depth_updates_per_sec, cache_hitrate
ticks_per_sec = ws_ticks_per_sec
depth_updates_per_sec = ws_depth_updates_per_sec
cache_hitrate = scanner_cache_hitrate
