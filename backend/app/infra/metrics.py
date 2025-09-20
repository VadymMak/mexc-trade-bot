# app/infra/metrics.py
from __future__ import annotations
from prometheus_client import Counter, Histogram, Gauge

# ───────────────────────── WS metrics ─────────────────────────
ticks_total = Counter(
    "ws_ticks_total", "Total parsed book-ticker updates", ["symbol"]
)

ws_lag_seconds = Histogram(
    "ws_lag_seconds",
    "Book-ticker lag (receive_now - send_time), seconds",
    ["symbol"],
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5, 10),
)

ws_reconnects_total = Counter(
    "ws_reconnects_total", "Total WebSocket reconnects"
)

ws_active_subscriptions = Gauge(
    "ws_active_subscriptions", "Active WS subscriptions topics count"
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

# Count of currently running per-symbol strategy loops.
# No labels to keep cardinality low. If you later want to split paper/live,
# you can add a {mode="paper|live"} label, but keep it lean if possible.
strategy_symbols_running = Gauge(
    "strategy_symbols_running",
    "Number of active strategy symbol loops"
)

# ───────────────────── Strategy histograms ─────────────────────
# NOTE: Prometheus histograms are non-negative. For PnL in bps (which can be negative),
# we record ABSOLUTE magnitude here; sign can be inferred from exits distribution or logs.
# If you want sign counts later, we can add a tiny counter with {sign="win|loss|flat"}.
strategy_trade_pnl_bps = Histogram(
    "strategy_trade_pnl_bps",
    "Absolute realized PnL per trade, in basis points (sign not captured here).",
    ["symbol"],
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0, 20.0, 50.0)
)

strategy_trade_duration_seconds = Histogram(
    "strategy_trade_duration_seconds",
    "Trade holding time (entry→exit), seconds.",
    ["symbol"],
    buckets=(0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 10, 20, 30, 60, 120, 300, 600)
)

strategy_edge_bps_at_entry = Histogram(
    "strategy_edge_bps_at_entry",
    "Edge at entry decision time, in basis points.",
    ["symbol"],
    buckets=(0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0)
)
