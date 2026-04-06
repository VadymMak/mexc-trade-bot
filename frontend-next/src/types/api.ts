/* ─────────────────────────────── Quotes / L2 ─────────────────────────────── */

export type Level = readonly [price: number, qty: number];

export interface Quote {
  symbol: string;          // "BTCUSDT"
  bid: number;             // best bid (0 if missing)
  ask: number;             // best ask (0 if missing)
  mid: number;             // (bid+ask)/2 when both >0, else fallback
  spread_bps: number;      // (ask-bid)/mid * 10_000
  ts?: number;             // epoch ms (frontend may normalize ts_ms → ts)
  ts_ms?: number;          // epoch ms
  bidQty?: number;
  askQty?: number;
  bids?: ReadonlyArray<Level>;
  asks?: ReadonlyArray<Level>;
  imbalance?: number;      // 0..1
}

/* ─────────────────────────────── Positions ─────────────────────────────── */

export interface Position {
  /** Canonical identity & size */
  symbol: string;
  qty: number;

  /** Canonical backend fields (preferred) */
  avg_price?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;

  /** Legacy UI aliases (kept optional for compatibility) */
  avg?: number;
  rpnl?: number;
  upnl?: number;

  /** Misc/compat */
  account_id?: string;
  exchange?: string;
  ts_ms?: number;

  /** Forward-compat */
  [k: string]: unknown;
}

/* ───────────────────────────── Strategy Metrics ─────────────────────────── */

export interface StrategyMetricsJSON {
  entries: Record<string, number>;
  exits: Record<string, { TP?: number; SL?: number; TIMEOUT?: number }>;
  open_positions: Record<string, number>;
  realized_pnl: Record<string, number>;
}

/* ───────────────────────────── Orders / Fills ───────────────────────────── */

export type Side = "BUY" | "SELL";
export type Liquidity = "MAKER" | "TAKER";
export type OrderStatus =
  | "NEW"
  | "PARTIALLY_FILLED"
  | "FILLED"
  | "CANCELED"
  | "REJECTED"
  | "EXPIRED"
  | string; // forward-compat

export interface OrderItem {
  id: string | number;
  symbol: string;
  side: Side;
  qty: number;
  price: number | null | undefined;   // LIMIT: number; MARKET: null/undefined
  status: OrderStatus;
  tag?: string | null;
  ts_ms?: number;
  is_active?: boolean;
  filled_qty?: number;
  avg_fill_price?: number | null;
  client_order_id?: string | null;
  exchange_order_id?: string | null;
}

export interface FillItem {
  id: string | number;
  order_id?: string | number | null;
  symbol: string;
  side: Side;
  qty: number;
  price: number;
  fee?: number;
  ts_ms?: number;
  trade_id?: string | null;
  client_order_id?: string | null;
  exchange_order_id?: string | null;
  is_maker?: boolean;
  liquidity?: Liquidity;
}

/* ─────────────────────────────── UI / Strategy ──────────────────────────── */

export interface UIState {
  watchlist?: string[];
  data?: { watchlist?: string[]; [k: string]: unknown };
  layout?: unknown;
  ui_prefs?: unknown;
  revision?: number;
  updated_at?: string; // ISO8601
}

export interface StrategyState {
  per_symbol?: Record<string, unknown>;
  revision?: number;
  updated_at?: string; // ISO8601
}

export interface UISnapshot {
  ui_state?: UIState;
  strategy_state?: StrategyState;
  positions?: Position[];
  orders?: OrderItem[];
  fills?: FillItem[];
}

/* ─────────────────────────────── SSE payloads ───────────────────────────── */

export interface StreamSnapshot {
  type: "snapshot";
  quotes: Quote[];
}

export interface StreamQuotes {
  type: "quotes";
  quotes: Quote[];
}

export interface StreamPing {
  type: "ping";
  ts: number;
}

export type StreamMessage = StreamSnapshot | StreamQuotes | StreamPing;

/* Legacy table row for some UIs */
export interface TickerRow {
  symbol: string;
  bid?: number;
  ask?: number;
  bid_qty?: number;
  ask_qty?: number;
}
