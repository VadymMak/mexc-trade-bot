import { create } from "zustand";
import { apiStartSymbols, apiStopAll, apiStopSymbols } from "@/hooks/useApi";
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
    const s = raw.trim().toUpperCase();
    if (!s) continue;
    seen.add(s);
  }
  return Array.from(seen);
}

export const useStrategy = create<StrategyState>((set) => ({
  busy: false,

  start: async (symbols) => {
    const syms = normList(symbols);
    if (!syms.length) return;
    set({ busy: true });
    try {
      await apiStartSymbols(syms);
      // локально отмечаем running = true (только если реально меняется)
      const { start, add } = useSymbols.getState();
      // если символ не в списке карточек — добавим его
      const existing = new Set(useSymbols.getState().items.map((i) => i.symbol));
      for (const s of syms) {
        if (!existing.has(s)) add(s);
        start(s);
      }
    } finally {
      set({ busy: false });
    }
  },

  stop: async (symbols, flatten = false) => {
    const syms = normList(symbols);
    if (!syms.length) return;
    set({ busy: true });
    try {
      await apiStopSymbols(syms, flatten);
      const { stop } = useSymbols.getState();
      for (const s of syms) stop(s);
    } finally {
      set({ busy: false });
    }
  },

  stopAll: async (flatten = false) => {
    set({ busy: true });
    try {
      await apiStopAll(flatten);
      const { stopAll } = useSymbols.getState();
      stopAll(); // локально погасим все «running»
    } finally {
      set({ busy: false });
    }
  },
}));
