import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { normalizeSymbol } from "../utils/format";  // NEW import

/** Элемент в списке карточек */
export type SymbolItem = {
  symbol: string;   // "ETHUSDT"
  running: boolean; // флаг для UI (только UI-индикатор, не автозапуск!)
};

export type SymbolsState = {
  /** Стабильный массив; ссылку меняем только при реальном изменении */
  items: SymbolItem[];

  // действия над одним символом
  add: (raw: string) => void;
  remove: (symbol: string) => void;
  start: (symbol: string) => void;
  stop: (symbol: string) => void;

  // групповые действия
  addSymbols: (symbols: string[]) => void;
  ensureSymbols: (symbols: string[]) => void;
  startAll: () => void;
  stopAll: () => void;
  flattenAll: () => void; // заглушка — фактический флэттен делает useStrategy

  // NEW: атомарно заменить весь список (используется на boot из backend)
  replace: (items: SymbolItem[]) => void;

  // (опционально, бывает удобно)
  clear?: () => void;
};

// helper: deduplicate + normalize
const dedupeNormalized = (list: string[]) => {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const s of list) {
    const v = normalizeSymbol(s);
    if (v && !seen.has(v)) {
      seen.add(v);
      out.push(v);
    }
  }
  return out;
};

export const useSymbols = create<SymbolsState>()(
  persist(
    (set, get) => ({
      items: [],

      add: (raw) => {
        const sym = normalizeSymbol(raw);
        if (!sym) return;
        const { items } = get();
        if (items.some((x) => x.symbol === sym)) return;
        set({ items: [...items, { symbol: sym, running: false }] });
      },

      remove: (symbol) => {
        const sym = normalizeSymbol(symbol);
        const { items } = get();
        const next = items.filter((x) => x.symbol !== sym);
        if (next.length === items.length) return; // no change
        set({ items: next });
      },

      start: (symbol) => {
        const sym = normalizeSymbol(symbol);
        const { items } = get();
        let changed = false;
        const next = items.map((x) => {
          if (x.symbol !== sym || x.running) return x;
          changed = true;
          return { ...x, running: true };
        });
        if (!changed) return;
        set({ items: next });
      },

      stop: (symbol) => {
        const sym = normalizeSymbol(symbol);
        const { items } = get();
        let changed = false;
        const next = items.map((x) => {
          if (x.symbol !== sym || !x.running) return x;
          changed = true;
          return { ...x, running: false };
        });
        if (!changed) return;
        set({ items: next });
      },

      addSymbols: (symbols) => {
        const toAdd = dedupeNormalized(symbols);
        if (!toAdd.length) return;
        const { items } = get();
        const existing = new Set(items.map((x) => x.symbol));
        const append: SymbolItem[] = [];
        for (const sym of toAdd) {
          if (!existing.has(sym)) append.push({ symbol: sym, running: false });
        }
        if (!append.length) return;
        set({ items: [...items, ...append] });
      },

      ensureSymbols: (symbols) => {
        const toEnsure = dedupeNormalized(symbols);
        if (!toEnsure.length) return;
        const { items } = get();
        const existing = new Set(items.map((x) => x.symbol));
        const append: SymbolItem[] = [];
        for (const sym of toEnsure) {
          if (!existing.has(sym)) append.push({ symbol: sym, running: false });
        }
        if (!append.length) return;
        set({ items: [...items, ...append] });
      },

      startAll: () => {
        const { items } = get();
        let changed = false;
        const next = items.map((x) => {
          if (x.running) return x;
          changed = true;
          return { ...x, running: true };
        });
        if (!changed) return;
        set({ items: next });
      },

      stopAll: () => {
        const { items } = get();
        let changed = false;
        const next = items.map((x) => {
          if (!x.running) return x;
          changed = true;
          return { ...x, running: false };
        });
        if (!changed) return;
        set({ items: next });
      },

      // здесь не меняем items (UI), только резервируем действие
      flattenAll: () => {},

      // NEW: полная замена списка (с нормализацией и дедупликацией)
      replace: (items) => {
        const seen = new Set<string>();
        const next: SymbolItem[] = [];
        for (const it of items ?? []) {
          const sym = normalizeSymbol(it.symbol ?? "");
          if (!sym || seen.has(sym)) continue;
          seen.add(sym);
          next.push({ symbol: sym, running: !!it.running });
        }
        set({ items: next });
      },

      // optional helper
      clear: () => set({ items: [] }),
    }),
    {
      name: "symbols-store-v1",
      storage: createJSONStorage(() => localStorage),
      // сохраняем только items (никаких функций)
      partialize: (state) => ({ items: state.items }),
      version: 1,
      migrate: (persisted) => {
        // на будущее: миграции, если менялась форма items
        return persisted as unknown as { items: SymbolItem[] };
      },
    }
  )
);

/** Удобные селекторы */
export const useSymbolItems = () => useSymbols((s) => s.items);
export const useSymbolRunning = (symbol: string) =>
  useSymbols(
    (s) => s.items.find((x) => x.symbol === normalizeSymbol(symbol))?.running ?? false
  );
export const useSymbolActions = () =>
  useSymbols((s) => ({
    add: s.add,
    remove: s.remove,
    start: s.start,
    stop: s.stop,
  }));
