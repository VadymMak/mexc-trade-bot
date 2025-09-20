import { create } from "zustand";

/** Элемент в списке карточек */
export type SymbolItem = {
  symbol: string;   // "ATHUSDT"
  running: boolean; // флаг для UI
};

export type SymbolsState = {
  items: SymbolItem[];

  // действия над одним символом
  add: (raw: string) => void;
  remove: (symbol: string) => void;
  start: (symbol: string) => void;
  stop: (symbol: string) => void;

  // групповые действия
  startAll: () => void;
  stopAll: () => void;
  flattenAll: () => void; // (заглушка — фактический флэттен делает useStrategy)
};

/** Нормализация тикера */
export function normSymbol(raw: string): string {
  return raw.trim().toUpperCase().replace(/\s+/g, "");
}

/**
 * Главный хук стора.
 * В компонентах ИСПОЛЬЗУЕМ ЕГО ТАК:
 *   const remove = useSymbols(s => s.remove)
 *   const items  = useSymbols(s => s.items)
 * Не деструктурируем напрямую: const { remove } = useSymbols()  <-- так лучше не делать
 */
export const useSymbols = create<SymbolsState>((set, get) => ({
  items: [],

  add: (raw) => {
    const sym = normSymbol(raw);
    if (!sym) return;
    const exists = get().items.some((x) => x.symbol === sym);
    if (exists) return;
    set((s) => ({ items: [...s.items, { symbol: sym, running: false }] }));
  },

  remove: (symbol) => {
    const sym = normSymbol(symbol);
    set((s) => {
      const next = s.items.filter((x) => x.symbol !== sym);
      return next.length === s.items.length ? s : { items: next };
    });
  },

  start: (symbol) => {
    const sym = normSymbol(symbol);
    set((s) => {
      let changed = false;
      const next = s.items.map((x) => {
        if (x.symbol !== sym || x.running) return x;
        changed = true;
        return { ...x, running: true };
      });
      return changed ? { items: next } : s;
    });
  },

  stop: (symbol) => {
    const sym = normSymbol(symbol);
    set((s) => {
      let changed = false;
      const next = s.items.map((x) => {
        if (x.symbol !== sym || !x.running) return x;
        changed = true;
        return { ...x, running: false };
      });
      return changed ? { items: next } : s;
    });
  },

  startAll: () =>
    set((s) => {
      let changed = false;
      const next = s.items.map((x) => {
        if (x.running) return x;
        changed = true;
        return { ...x, running: true };
      });
      return changed ? { items: next } : s;
    }),

  stopAll: () =>
    set((s) => {
      let changed = false;
      const next = s.items.map((x) => {
        if (!x.running) return x;
        changed = true;
        return { ...x, running: false };
      });
      return changed ? { items: next } : s;
    }),

  // здесь не меняем items (UI), только резервируем действие
  flattenAll: () => {},
}));

/** Удобные селекторы (по желанию) — помогают не держать ссылку на весь стор */
export const useSymbolItems = () => useSymbols((s) => s.items);
export const useSymbolRunning = (symbol: string) =>
  useSymbols((s) => s.items.find((x) => x.symbol === normSymbol(symbol))?.running ?? false);
export const useSymbolActions = () =>
  useSymbols((s) => ({ add: s.add, remove: s.remove, start: s.start, stop: s.stop }));
