export interface ScannerRow {
  exchange?: "gate" | "mexc";
  symbol: string;
  bid: number;
  ask: number;
  last?: number;
  spread_abs?: number;
  spread_pct?: number;   // % (0.10 = 10 bps)
  spread_bps?: number;   // = spread_pct*100
  base_volume_24h?: number;
  quote_volume_24h?: number;
  depth5_bid_usd?: number;
  depth5_ask_usd?: number;
  depth10_bid_usd?: number;
  depth10_ask_usd?: number;
  trades_per_min?: number;
  usd_per_min?: number;
  median_trade_usd?: number;
  imbalance?: number;    // 0..1
  ws_lag_ms?: number;
  ts_ms?: number;
  reason?: string | null;
  reasons_all?: string[];
}

export interface GetScannerOpts {
  quote?: string;            // default "USDT"
  minBps?: number;           // UI bps (2 → 0.02%)
  minUsd?: number;           // 24h quote vol USD
  limit?: number;            // result limit
  includeStables?: boolean;
  excludeLeveraged?: boolean;
  minDepth5Usd?: number;
  minDepth10Usd?: number;
  minTradesPerMin?: number;
  minUsdPerMin?: number;
  explain?: boolean;
}

export type Tier = "A" | "B" | "Excluded";

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
}

export interface FeeInfo {
  maker?: number | null;
  taker?: number | null;
  zero_maker: boolean;
}

export interface FeatureSnapshot {
  ts: number;
  venue: "gate" | "mexc";
  symbol: string;
  preset: string;
  metrics: MetricsMini;
  score: number;           // 0..100
  tier: Tier;
  reasons: string[];
  stale: boolean;
  fees: FeeInfo;
}

export interface ScannerTopTieredResponse {
  ts: number;
  preset: string;
  tierA: FeatureSnapshot[];
  tierB: FeatureSnapshot[];
  excluded: FeatureSnapshot[];
}
