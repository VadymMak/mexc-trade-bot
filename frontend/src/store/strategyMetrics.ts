import { create } from "zustand";
import http from "@/lib/http";

interface StrategyMetrics {
  entries: Record<string, number>;
  exits: Record<string, Record<string, number>>;
  open_positions: Record<string, number>;
  realized_pnl: Record<string, number>;
}

interface StrategyMetricsState {
  metrics: StrategyMetrics | null;
  loading: boolean;
  error: string | null;
  loadMetrics: () => Promise<void>;
  isSymbolRunning: (symbol: string) => boolean;
}

export const useStrategyMetrics = create<StrategyMetricsState>((set, get) => ({
  metrics: null,
  loading: false,
  error: null,

  loadMetrics: async () => {
    try {
      set({ loading: true, error: null });

      const res = await http.get<StrategyMetrics>("/api/strategy/metrics");
      set({ metrics: res.data, loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to load strategy metrics",
        loading: false,
      });
    }
  },

  isSymbolRunning: (symbol: string): boolean => {
    const metrics = get().metrics;
    if (!metrics) return false;
    
    const sym = symbol.toUpperCase();
    return Object.prototype.hasOwnProperty.call(metrics.entries, sym);
  },
}));