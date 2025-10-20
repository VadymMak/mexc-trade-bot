import { create } from "zustand";

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

      const res = await fetch("/api/strategy/metrics", {
        method: "GET",
        headers: { Accept: "application/json" },
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`GET /api/strategy/metrics failed: ${res.status} ${text}`);
      }

      const data: StrategyMetrics = await res.json();
      set({ metrics: data, loading: false });
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