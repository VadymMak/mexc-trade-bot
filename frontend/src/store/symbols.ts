// src/store/symbols.ts
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { normalizeSymbol, isValidSymbol, parseSymbolsInput } from "@/utils/format";

/** Элемент в списке карточек */
export type SymbolItem = {
  symbol: string;   // "ETHUSDT"
  running: boolean; // флаг для UI (индикатор, не автозапуск)
};

export type SymbolsState = {
  items: SymbolItem[];

  // single
  add: (raw: string) => void;
  remove: (symbol: string) => void;
  start: (symbol: string) => void;
  stop: (symbol: string) => void;

  // group
  addSymbols: (symbols: string[]) => void;
  ensureSymbols: (symbols: string[]) => void;
  startAll: () => void;
  stopAll: () => void;
  flattenAll: () => void;

  // replace all (minimal-diff)
  replace: (items: SymbolItem[]) => void;

  clear?: () => void;
};

/* -------------------- helpers -------------------- */
const dedupeNormalizedValid = (list: string[]): string[] => {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const s of list) {
    const v = normalizeSymbol(s);
    if (v && isValidSymbol(v) && !seen.has(v)) {
      seen.add(v);
      out.push(v);
    }
  }
  return out;
};

const arraysShallowEqual = (a: SymbolItem[], b: SymbolItem[]): boolean => {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (x.symbol !== y.symbol || x.running !== y.running) return false;
  }
  return true;
};

/* -------------------- store -------------------- */
export const useSymbols = create<SymbolsState>()(
  persist(
    (set, get) => ({
      items: [],

      add: (raw) => {
        const sym = normalizeSymbol(raw);
        if (!sym || !isValidSymbol(sym)) return;
        const { items } = get();
        if (items.some((x) => x.symbol === sym)) return;
        set({ items: [...items, { symbol: sym, running: false }] });
      },

      remove: (symbol) => {
        const sym = normalizeSymbol(symbol);
        const { items } = get();
        const next = items.filter((x) => x.symbol !== sym);
        if (next.length === items.length) return;
        set({ items: next });
      },

      start: (symbol) => {
        const sym = normalizeSymbol(symbol);
        if (!isValidSymbol(sym)) return;
        const { items } = get();
        let changed = false;
        const next = items.map((x) => {
          if (x.symbol !== sym || x.running) return x;
          changed = true;
          return { ...x, running: true };
        });
        if (changed) set({ items: next });
      },

      stop: (symbol) => {
        const sym = normalizeSymbol(symbol);
        if (!isValidSymbol(sym)) return;
        const { items } = get();
        let changed = false;
        const next = items.map((x) => {
          if (x.symbol !== sym || !x.running) return x;
          changed = true;
          return { ...x, running: false };
        });
        if (changed) set({ items: next });
      },

      addSymbols: (symbols) => {
        const parsed = parseSymbolsInput(symbols.join(" "));
        const toAdd = dedupeNormalizedValid(parsed.good);
        if (!toAdd.length) return;

        const { items } = get();
        const existing = new Set(items.map((x) => x.symbol));
        const append: SymbolItem[] = [];
        for (const sym of toAdd) {
          if (!existing.has(sym)) append.push({ symbol: sym, running: false });
        }
        if (append.length) set({ items: [...items, ...append] });
      },

      ensureSymbols: (symbols) => {
        const parsed = parseSymbolsInput(symbols.join(" "));
        const toEnsure = dedupeNormalizedValid(parsed.good);
        if (!toEnsure.length) return;

        const { items } = get();
        const existing = new Set(items.map((x) => x.symbol));
        const append: SymbolItem[] = [];
        for (const sym of toEnsure) {
          if (!existing.has(sym)) append.push({ symbol: sym, running: false });
        }
        if (append.length) set({ items: [...items, ...append] });
      },

      startAll: () => {
        const { items } = get();
        let changed = false;
        const next = items.map((x) => {
          if (x.running) return x;
          changed = true;
          return { ...x, running: true };
        });
        if (changed) set({ items: next });
      },

      stopAll: () => {
        const { items } = get();
        let changed = false;
        const next = items.map((x) => {
          if (!x.running) return x;
          changed = true;
          return { ...x, running: false };
        });
        if (changed) set({ items: next });
      },

      flattenAll: () => {
        // handled in strategy/api layer; kept for interface symmetry
      },

      // Minimal-diff replace that preserves existing order where possible
      replace: (incoming) => {
        // 1) sanitize incoming
        const incSeen = new Set<string>();
        const incMap = new Map<string, boolean>();
        const incOrder: string[] = [];
        for (const it of incoming ?? []) {
          const sym = normalizeSymbol(it?.symbol ?? "");
          if (!sym || !isValidSymbol(sym) || incSeen.has(sym)) continue;
          incSeen.add(sym);
          incOrder.push(sym);
          incMap.set(sym, !!it?.running);
        }

        const { items: cur } = get();
        // 2) keep existing order for retained symbols
        const kept: SymbolItem[] = [];
        const added: SymbolItem[] = [];
        const keptSet = new Set<string>();

        for (const it of cur) {
          const sym = it.symbol;
          if (incMap.has(sym)) {
            kept.push({
              symbol: sym,
              running: incMap.get(sym)!,
            });
            keptSet.add(sym);
          }
          // symbols not in incoming are dropped
        }

        // 3) append genuinely new symbols in incoming order
        for (const sym of incOrder) {
          if (!keptSet.has(sym)) {
            added.push({ symbol: sym, running: incMap.get(sym)! });
          }
        }

        const next = [...kept, ...added];

        // 4) only set when truly changed to avoid SSE reconnects
        if (!arraysShallowEqual(cur, next)) {
          set({ items: next });
        }
      },

      clear: () => set({ items: [] }),
    }),
    {
      name: "symbols-store-v1",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ items: state.items }),
      version: 2,
      migrate: (persisted) => {
        const old = (persisted as unknown as { items?: SymbolItem[] })?.items ?? [];
        const seen = new Set<string>();
        const items: SymbolItem[] = [];
        for (const it of old) {
          const sym = normalizeSymbol(it?.symbol ?? "");
          if (!sym || !isValidSymbol(sym) || seen.has(sym)) continue;
          seen.add(sym);
          items.push({ symbol: sym, running: !!it?.running });
        }
        return { items };
      },
    }
  )
);

export const useSymbolItems = () => useSymbols((s) => s.items);
export const useSymbolRunning = (symbol: string) =>
  useSymbols((s) => s.items.find((x) => x.symbol === normalizeSymbol(symbol))?.running ?? false);
export const useSymbolActions = () =>
  useSymbols((s) => ({
    add: s.add,
    remove: s.remove,
    start: s.start,
    stop: s.stop,
  }));
