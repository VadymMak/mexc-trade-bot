import http from "@/lib/http";
import type { StrategyMetricsJSON, Position } from "@/types/api";

export async function apiStartSymbols(symbols: string[]) {
  await http.post("/api/strategy/start", { symbols });
}

export async function apiStopSymbols(symbols: string[], flatten = false) {
  await http.post("/api/strategy/stop", { symbols, flatten });
}

export async function apiStopAll(flatten = false) {
  await http.post("/api/strategy/stop-all", { flatten });
}

export async function apiGetPositions(symbols: string[]): Promise<Position[]> {
  const params = new URLSearchParams();
  for (const s of symbols) params.append("symbols", s);
  const res = await http.get<Position[]>("/api/strategy/positions", { params });
  return res.data;
}

export async function apiGetMetrics(): Promise<StrategyMetricsJSON> {
  const res = await http.get<StrategyMetricsJSON>("/api/strategy/metrics");
  return res.data;
}
