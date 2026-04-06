import type { Position } from "./api";

export interface StrategyParams {
  // Entry filters
  min_spread_bps: number;
  edge_floor_bps: number;
  imbalance_min: number;
  imbalance_max: number;
  enable_depth_check: boolean;
  absorption_x_bps: number;
  // Sizing & timing
  order_size_usd: number;
  timeout_exit_sec: number;
  max_concurrent_symbols: number;
  // Trade management
  take_profit_bps: number;
  stop_loss_bps: number;
  min_hold_ms: number;
  reenter_cooldown_ms: number;
  // Debug / test
  debug_force_entry: boolean;
}

export interface ExecPlaceResponse {
  ok: boolean;
  client_order_id?: string;
  position?: Position;
}
export interface ExecFlattenResponse {
  ok: boolean;
  flattened?: string;
  position?: Position;
}
export interface ExecCancelResponse {
  ok?: boolean;
}

export interface StrategyStartResponse {
  ok?: boolean;
  started?: string[];
  running?: string[];
  message?: string;
}
export interface StrategyStopResponse {
  ok?: boolean;
  stopped?: string[];
  flattened?: string[];
  running?: string[];
  message?: string;
}
export interface StopAllResponse {
  ok?: boolean;
  stopped?: string[];
  flattened?: string[];
  message?: string;
}
