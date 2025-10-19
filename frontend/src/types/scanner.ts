// src/types/scanner.ts

export type Venue = "gate" | "mexc";
export type Tier = "A" | "B" | "Excluded";

/** UI/type helpers used by scanner pages */
export type QuoteFilter = "ALL" | "USDT" | "USDC" | "FDUSD" | "BUSD";
export type ExchangeFilter = "gate" | "mexc" | "all";
export type Preset =
  | "metaskalp"
  | "hedgehog"
  | "balanced"
  | "scalper"
  | "ерш"
  | "conservative"
  | "aggressive";

/** One row from /api/scanner/{venue}/top */
export interface ScannerRow {
  exchange?: Venue;
  symbol: string;

  // prices
  bid: number;
  ask: number;
  last?: number;

  // raw spread
  spread_abs?: number;
  /** 0.10 = 10 bps */
  spread_pct?: number;
  /** spread_pct * 100 */
  spread_bps?: number;

  // effective spread with fees (server-computed)
  // canonical (maker/taker, bps + pct + abs)
  eff_spread_bps_maker?: number | null;
  eff_spread_pct_maker?: number | null;
  eff_spread_abs_maker?: number | null;

  eff_spread_bps_taker?: number | null;
  eff_spread_pct_taker?: number | null;
  eff_spread_abs_taker?: number | null;

  // legacy aliases (still returned by some printers)
  eff_spread_maker_bps?: number | null;
  eff_spread_taker_bps?: number | null;

  // fees
  maker_fee?: number | null;
  taker_fee?: number | null;
  zero_fee?: boolean | null;

  // 24h stats
  base_volume_24h?: number;
  quote_volume_24h?: number;

  // depth (flat)
  depth5_bid_usd?: number;
  depth5_ask_usd?: number;
  depth10_bid_usd?: number;
  depth10_ask_usd?: number;

  // depth (map for arbitrary bps levels)
  depth_at_bps?: Record<number, { bid_usd?: number; ask_usd?: number }>;

  // tape/flow
  /** trades per minute */
  trades_per_min?: number;
  /** USD per minute */
  usd_per_min?: number;
  /** median trade size in USD */
  median_trade_usd?: number;

  /** 0..1 */
  imbalance?: number;

  // telemetry
  ws_lag_ms?: number;
  ts_ms?: number;

  // scoring/tiering (optional in /top)
  score?: number; // 0..100
  tier?: Tier;

  // explainability
  reason?: string | null;
  reasons_all?: string[];
}

/** Client and server parameters for /top and /top_tiered */
export interface GetScannerOpts {
  // server-native (pass as query)
  /** e.g. "USDT" (default) */
  quote?: string;
  /** hard server-side limit */
  limit?: number;

  /** server preset: "hedgehog" | "balanced" | "metaskalp" | ... */
  preset?: string;
  /** request candle features on server (ATR, spikes, etc.) */
  fetch_candles?: boolean;
  /** depth levels in bps: [5,10] -> "5,10" */
  depth_bps_levels?: number[];
  /** enable symbol rotation hints on server */
  rotation?: boolean;
  /** include reasons/diagnostics in response */
  explain?: boolean;

  /** ---- server-side filters (supported by backend) ---- */
  /** maximum allowed spread in bps (e.g. 10) */
  max_spread_bps?: number;
  /** 24h quote volume USD minimum (server filter) */
  min_quote_vol_usd?: number;
  /** USD per minute minimum */
  min_usd_per_min?: number;
  /** minimum median trade size in USD */
  min_median_trade_usd?: number;
  /** minimum vol-pattern score (0–100) */
  min_vol_pattern?: number;
  /** cap for ATR proxy */
  max_atr_proxy?: number;
  /** require usd_per_min >= activity_ratio * depth5_min_side */
  activity_ratio?: number;

  /** minimum depth within ±5/10 bps */
  min_depth5_usd?: number;
  min_depth10_usd?: number;

  /** minimum trades per minute */
  min_trades_per_min?: number;

  /** include stables as base assets */
  include_stables?: boolean;
  /** exclude leveraged tokens (3L/3S/UP/DOWN) */
  exclude_leveraged?: boolean;

  /** whitelist of symbols (CSV or array), e.g. "BTCUSDT,ETHUSDT" */
  symbols?: string | string[];

  /** ---- front-end legacy names kept for compatibility (optional) ---- */
  /** UI bps threshold (kept for FE, not used if max_spread_bps is set) */
  minBps?: number;
  /** UI-only alias of min_quote_vol_usd */
  minUsd?: number;
}

/** Compact metrics snapshot used in tiered responses */
export interface MetricsMini {
  usd_per_min: number;
  trades_per_min: number;
  effective_spread_bps: number;
  slip_bps_clip: number;
  atr1m_pct: number;
  spike_count_90m: number;
  pullback_median_retrace: number;
  grinder_ratio: number;
  depth_usd_5bps: number;
  imbalance_sigma_hits_60m: number;
  ws_lag_ms?: number | null;
  stale_sec?: number | null;

  // Optional: if backend supplies maker/taker split
  eff_spread_maker_bps?: number | null;
  eff_spread_taker_bps?: number | null;
}

/** Fee information with source for transparency chips */
export interface FeeInfo {
  maker?: number | null;
  taker?: number | null;
  zero_maker: boolean;
  /** env_applied | map_applied | none */
  source?: "env_applied" | "map_applied" | "none";
}

/** One ranked/tiered snapshot item from /top_tiered */
export interface FeatureSnapshot {
  ts: number;
  venue: Venue;
  symbol: string;
  preset: string;

  metrics: MetricsMini;
  score: number; // 0..100
  tier: Tier;

  reasons: string[];
  reasons_all?: string[];

  stale: boolean;
  fees: FeeInfo;
}

/** Response shape from /api/scanner/{venue}/top_tiered */
export interface ScannerTopTieredResponse {
  ts: number;
  preset: string;
  tierA: FeatureSnapshot[];
  tierB: FeatureSnapshot[];
  excluded: FeatureSnapshot[];
}

/** Optional: plain /top response if you want a typed array wrapper */
export interface ScannerTopResponse {
  ts: number;
  preset?: string;
  rows: ScannerRow[];
}

/** UI row used by LiquidityScanner after local derivations */
export interface ScannerUiRow extends ScannerRow {
  // Store internal computed fields (prefixed with _)
  _bps: number;
  _mid: number;
  _minQty: number;
  _notionalNow: number;
  _notionalProxy: number;
  _quote: string;
  _base: string;
  _feeUnknown: boolean;
  _minDepthAtBps?: number;

  // Page-level computed fields (no prefix)
  mid: number;
  spread_bps_ui: number;
  daily_notional_usd: number;
  quote_ccy: string;
  base_ccy: string;
  fee_unknown: boolean;
}
