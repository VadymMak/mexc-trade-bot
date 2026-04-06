// ========== Trade History Types ==========

export interface Trade {
  id: number;
  trade_id: string;
  symbol: string;
  exchange: string;
  entry_time: string;
  entry_price: number;
  entry_qty: number;
  entry_side: string;
  exit_time?: string | null;
  exit_price?: number | null;
  exit_qty?: number | null;
  exit_side?: string | null;
  exit_reason?: string | null;
  pnl_usd: number;
  pnl_bps: number;
  pnl_percent: number;
  entry_fee: number;
  exit_fee: number;
  total_fee: number;
  hold_duration_sec?: number | null;
  spread_bps_entry?: number | null;
  imbalance_entry?: number | null;
  depth_5bps_entry?: number | null;
  strategy_tag?: string | null;
  strategy_params?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface TradeStats {
  period: string;
  days: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  trading_pnl: number;
  trading_fees: number;
  gross_profit: number;
  avg_profit_per_trade: number;
  avg_duration_sec: number;
  best_trade: number;
  worst_trade: number;
  infrastructure_costs?: number;
  net_profit?: number;
  costs_covered?: boolean;
  breakeven_days?: number;
}