import { create } from "zustand";
import { apiStartSymbols, apiStopAll, apiStopSymbols } from "@/api/api";
import { useSymbols } from "@/store/symbols";

type StrategyState = {
  busy: boolean;
  start: (symbols: string[]) => Promise<void>;
  stop: (symbols: string[], flatten?: boolean) => Promise<void>;
  stopAll: (flatten?: boolean) => Promise<void>;
};

function normList(list: string[]): string[] {
  const seen = new Set<string>();
  for (const raw of list) {
    const s = String(raw ?? "").trim().toUpperCase();
    if (s) seen.add(s);
  }
  return Array.from(seen);
}

export const useStrategy = create<StrategyState>((set, get) => ({
  busy: false,

  start: async (symbols) => {
    const syms = normList(symbols);
    if (syms.length === 0) return;

    // prevent overlapping actions
    if (get().busy) return;
    set({ busy: true });

    try {
      await apiStartSymbols(syms);

      // Reflect UI state:
      // 1) ensure cards exist (single-pass; no re-renders per item)
      // 2) mark only changed items as running (per-item, but guarded)
      const symStore = useSymbols.getState();
      symStore.ensureSymbols(syms);

      for (const s of syms) {
        symStore.start(s); // start() is already no-op if it's already running
      }
    } finally {
      set({ busy: false });
    }
  },

  stop: async (symbols, flatten = false) => {
    const syms = normList(symbols);
    if (syms.length === 0) return;

    if (get().busy) return;
    set({ busy: true });

    try {
      await apiStopSymbols(syms, flatten);

      // Flip running flags only for these symbols
      const symStore = useSymbols.getState();
      for (const s of syms) symStore.stop(s); // stop() is no-op if already stopped
    } finally {
      set({ busy: false });
    }
  },

  stopAll: async (flatten = false) => {
    if (get().busy) return;
    set({ busy: true });

    try {
      await apiStopAll(flatten);

      // Single store update instead of per-symbol loop
      const symStore = useSymbols.getState();
      symStore.stopAll();
    } finally {
      set({ busy: false });
    }
  },
}));
