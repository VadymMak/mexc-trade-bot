// ---- Общие типы котировок ----
export type Level = readonly [number, number]; // [price, qty] для L2 (tuple readonly)

export type Quote = {
  symbol: string;
  bid: number;
  ask: number;
  mid: number;
  spread_bps: number;

  /** Время: поддерживаем оба варианта, FE нормализует ts_ms → ts */
  ts?: number;
  ts_ms?: number;

  /** Необязательные L1 объёмы (если бэкенд их присылает) */
  bidQty?: number;
  askQty?: number;

  /** Необязательные L2 уровни (readonly для согласованности по всему FE) */
  bids?: ReadonlyArray<Level>;
  asks?: ReadonlyArray<Level>;

  /** Доп. метрики по желанию бэкенда/UI */
  imbalance?: number;
};

// ---- Позиции ----
export type Position = {
  symbol: string;
  qty: number;

  /** Бэкенд-стиль */
  avg_price?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  ts_ms?: number;

  /** FE-алиасы (для совместимости с существующими панелями) */
  avg?: number;
  upnl?: number;
  rpnl?: number;
  ts?: number;
};

// ---- Метрики стратегии ----
export type StrategyMetricsJSON = {
  entries: Record<string, number>;
  exits: Record<string, { TP?: number; SL?: number; TIMEOUT?: number }>;
  open_positions: Record<string, number>;
  realized_pnl: Record<string, number>;
};

// ---- Типы для Orders / Fills ----
export type OrderItem = {
  id: string | number;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  /** LIMIT: число; MARKET: null/undefined */
  price: number | null | undefined;
  status: string; // NEW | PARTIALLY_FILLED | FILLED | CANCELED | ...
  tag?: string | null;
  /** мс-таймстамп, если есть (например, из submitted_at/last_event_at); может отсутствовать */
  ts_ms?: number;

  // опциональные поля
  is_active?: boolean;
  filled_qty?: number;
  avg_fill_price?: number | null;
  client_order_id?: string | null;
  exchange_order_id?: string | null;
};

export type FillItem = {
  id: string | number;
  /** может не прийти в некоторых БД (тогда null/undefined) */
  order_id?: string | number | null;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  fee?: number;

  /** мс-таймстамп исполнения; предпочтительнее single-число для сортировки на FE */
  ts_ms?: number;

  // опциональные поля
  trade_id?: string | null;
  client_order_id?: string | null;
  exchange_order_id?: string | null;
  is_maker?: boolean;
  liquidity?: "MAKER" | "TAKER";
};

// ---- UI/Strategy State (из /api/ui/snapshot) ----
export type UIState = {
  /** Канонический список, если бэкенд кладёт прямо сюда */
  watchlist?: string[];

  /** Часто бэкенд вкладывает внутрь data */
  data?: {
    watchlist?: string[];
    [k: string]: unknown;
  };

  layout?: unknown;
  ui_prefs?: unknown;
  revision?: number;
  updated_at?: string; // ISO string
};

export type StrategyState = {
  per_symbol?: Record<string, unknown>;
  revision?: number;
  updated_at?: string; // ISO string
};

// ---- UI Snapshot ----
export type UISnapshot = {
  ui_state?: UIState;
  strategy_state?: StrategyState;

  /** Опциональные секции, если запрошены include=positions,orders,fills */
  positions?: Position[];
  orders?: OrderItem[];
  fills?: FillItem[];
};

// ---- SSE stream payloads ----
export type StreamSnapshot = {
  type: "snapshot";
  quotes: Quote[]; // initial full list
};

export type StreamQuotes = {
  type: "quotes";
  quotes: Quote[]; // periodic updates
};

export type StreamPing = {
  type: "ping";
  ts: number;
};

export type StreamMessage = StreamSnapshot | StreamQuotes | StreamPing;
