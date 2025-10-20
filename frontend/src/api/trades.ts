/**
 * API functions for trade history
 */

import type { Trade, TradeStats } from "@/types";

/**
 * Fetch recent trades with filters
 */
export async function fetchTrades(params: {
  limit?: number;
  symbol?: string;
  status?: string;
  period?: string;
}): Promise<Trade[]> {
  const searchParams = new URLSearchParams();
  
  if (params.limit) searchParams.set("limit", params.limit.toString());
  if (params.symbol) searchParams.set("symbol", params.symbol);
  if (params.status) searchParams.set("status", params.status);
  if (params.period) searchParams.set("period", params.period);

  const url = `/api/trades/recent?${searchParams.toString()}`;
  const res = await fetch(url);
  
  if (!res.ok) {
    throw new Error(`Failed to fetch trades: ${res.status}`);
  }
  
  return res.json();
}

/**
 * Fetch trade statistics
 */
export async function fetchTradeStats(params: {
  period?: string;
  include_costs?: boolean;
}): Promise<TradeStats> {
  const searchParams = new URLSearchParams();
  
  if (params.period) searchParams.set("period", params.period);
  if (params.include_costs !== undefined) {
    searchParams.set("include_costs", params.include_costs.toString());
  }

  const url = `/api/trades/stats?${searchParams.toString()}`;
  const res = await fetch(url);
  
  if (!res.ok) {
    throw new Error(`Failed to fetch trade stats: ${res.status}`);
  }
  
  return res.json();
}

/**
 * Export trades as CSV
 */
export function exportTradesCSV(params: {
  period?: string;
  symbol?: string;
  status?: string;
}): void {
  const searchParams = new URLSearchParams();
  
  if (params.period) searchParams.set("period", params.period);
  if (params.symbol) searchParams.set("symbol", params.symbol);
  if (params.status) searchParams.set("status", params.status);

  const url = `/api/trades/export?${searchParams.toString()}`;
  
  // Trigger download
  window.open(url, "_blank");
}