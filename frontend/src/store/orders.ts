// src/store/orders.ts
import { create } from "zustand";
import type { OrderItem, FillItem } from "@/types";

const HISTORY_CAP = 500; // per-symbol storage cap
const normSymbol = (s?: string) => String(s ?? "").trim().toUpperCase();

// Shared empties to prevent rerenders from new [] instances
const EMPTY_ORDERS: OrderItem[] = [];
const EMPTY_FILLS:  FillItem[]  = [];

// backend may occasionally send string times - support them w/o `any`
type OrderExtra = OrderItem & { submitted_at?: string; created_at?: string; updated_at?: string };
type FillExtra  = FillItem  & { executed_at?: string; created_at?: string; updated_at?: string };

const parseMaybeDate = (iso?: string): number => {
  if (!iso) return 0;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
};

const orderTs = (o: OrderExtra): number =>
  typeof o.ts_ms === "number"
    ? o.ts_ms
    : parseMaybeDate(o.submitted_at) ||
      parseMaybeDate(o.updated_at) ||
      parseMaybeDate(o.created_at);

const fillTs = (f: FillExtra): number =>
  typeof f.ts_ms === "number"
    ? f.ts_ms
    : parseMaybeDate(f.executed_at) ||
      parseMaybeDate(f.updated_at) ||
      parseMaybeDate(f.created_at);

/** Dedup by `id`, keep the item with the latest timestamp */
function dedupeByIdLatest<T extends { id: string | number }>(
  current: T[],
  incoming: T[],
  getTs: (x: T) => number
): T[] {
  const map = new Map<string | number, T>();

  // collapse duplicates already in `current`
  for (const it of current) {
    const prev = map.get(it.id);
    if (!prev || getTs(it) >= getTs(prev)) map.set(it.id, it);
  }

  // merge incoming, keeping newest
  for (const it of incoming) {
    const prev = map.get(it.id);
    if (!prev || getTs(it) >= getTs(prev)) map.set(it.id, it);
  }

  return Array.from(map.values());
}

type OrdersState = {
  /** Orders by symbol */
  orders: Record<string, OrderItem[]>;
  /** Fills by symbol */
  fills: Record<string, FillItem[]>;

  /** Clear all */
  clear: () => void;

  /** Merge from UI snapshot (dedupes by id, keeps latest by time) */
  setFromSnapshot: (payload: { orders?: OrderItem[]; fills?: FillItem[] }) => void;

  /** Optional: incremental upserts (for future streaming) */
  upsertOrders: (rows: OrderItem[]) => void;
  upsertFills: (rows: FillItem[]) => void;

  /** Selectors */
  ordersOf: (symbol: string) => OrderItem[];
  fillsOf: (symbol: string) => FillItem[];
};

export const useOrders = create<OrdersState>()((set, get) => ({
  orders: {},
  fills: {},

  clear: () => set({ orders: {}, fills: {} }),

  setFromSnapshot: ({ orders, fills }) =>
    set((state) => {
      // quick skip if payload is empty
      if ((!orders || orders.length === 0) && (!fills || fills.length === 0)) {
        return state;
      }

      const nextOrders: Record<string, OrderItem[]> = { ...state.orders };
      const nextFills: Record<string, FillItem[]> = { ...state.fills };

      if (orders && orders.length) {
        const grouped: Record<string, OrderItem[]> = {};
        for (const o of orders) {
          const sym = normSymbol(o.symbol);
          if (!sym) continue;
          (grouped[sym] ||= []).push(o);
        }
        for (const sym of Object.keys(grouped)) {
          const cur = nextOrders[sym] ?? EMPTY_ORDERS;
          const merged = dedupeByIdLatest(cur, grouped[sym], (x) => orderTs(x as OrderExtra))
            .sort((a, b) => orderTs(b as OrderExtra) - orderTs(a as OrderExtra))
            .slice(0, HISTORY_CAP);
          nextOrders[sym] = merged;
        }
      }

      if (fills && fills.length) {
        const grouped: Record<string, FillItem[]> = {};
        for (const f of fills) {
          const sym = normSymbol(f.symbol);
          if (!sym) continue;
          (grouped[sym] ||= []).push(f);
        }
        for (const sym of Object.keys(grouped)) {
          const cur = nextFills[sym] ?? EMPTY_FILLS;
          const merged = dedupeByIdLatest(cur, grouped[sym], (x) => fillTs(x as FillExtra))
            .sort((a, b) => fillTs(b as FillExtra) - fillTs(a as FillExtra))
            .slice(0, HISTORY_CAP);
          nextFills[sym] = merged;
        }
      }

      return { orders: nextOrders, fills: nextFills };
    }),

  upsertOrders: (rows) =>
    set((state) => {
      if (!rows?.length) return state;
      const grouped: Record<string, OrderItem[]> = {};
      for (const o of rows) {
        const sym = normSymbol(o.symbol);
        if (!sym) continue;
        (grouped[sym] ||= []).push(o);
      }
      const next = { ...state.orders };
      for (const sym of Object.keys(grouped)) {
        const cur = next[sym] ?? EMPTY_ORDERS;
        next[sym] = dedupeByIdLatest(cur, grouped[sym], (x) => orderTs(x as OrderExtra))
          .sort((a, b) => orderTs(b as OrderExtra) - orderTs(a as OrderExtra))
          .slice(0, HISTORY_CAP);
      }
      return { orders: next };
    }),

  upsertFills: (rows) =>
    set((state) => {
      if (!rows?.length) return state;
      const grouped: Record<string, FillItem[]> = {};
      for (const f of rows) {
        const sym = normSymbol(f.symbol);
        if (!sym) continue;
        (grouped[sym] ||= []).push(f);
      }
      const next = { ...state.fills };
      for (const sym of Object.keys(grouped)) {
        const cur = next[sym] ?? EMPTY_FILLS;
        next[sym] = dedupeByIdLatest(cur, grouped[sym], (x) => fillTs(x as FillExtra))
          .sort((a, b) => fillTs(b as FillExtra) - fillTs(a as FillExtra))
          .slice(0, HISTORY_CAP);
      }
      return { fills: next };
    }),

  ordersOf: (symbol) => {
    const sym = normSymbol(symbol);
    return get().orders[sym] ?? EMPTY_ORDERS;
  },
  fillsOf: (symbol) => {
    const sym = normSymbol(symbol);
    return get().fills[sym] ?? EMPTY_FILLS;
  },
}));

declare global {
  interface Window {
    __useOrders?: typeof useOrders;
  }
}

if (typeof window !== "undefined") {
  window.__useOrders = useOrders;
}
