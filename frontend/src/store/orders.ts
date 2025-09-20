// src/store/orders.ts
import { create } from "zustand";
import type {
  OrderItem as ApiOrderItem,
  FillItem as ApiFillItem,
} from "@/types/api";

/** Public types re-export */
export type OrderItem = ApiOrderItem;
export type FillItem = ApiFillItem;

type OrdersState = {
  /** Orders by symbol */
  orders: Record<string, OrderItem[]>;
  /** Fills by symbol */
  fills: Record<string, FillItem[]>;

  /** Merge from UI snapshot (dedupes by id, keeps latest by time) */
  setFromSnapshot: (payload: {
    orders?: OrderItem[];
    fills?: FillItem[];
  }) => void;
};

const HISTORY_CAP = 500; // per-symbol storage cap

const normSymbol = (s?: string) => String(s ?? "").trim().toUpperCase();

/** backend may occasionally send string times - support them without `any` */
type OrderExtra = OrderItem & { submitted_at?: string };
type FillExtra = FillItem & { executed_at?: string };

const parseMaybeDate = (iso?: string): number => {
  if (!iso) return 0;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
};

const orderTs = (o: OrderExtra): number =>
  typeof o.ts_ms === "number" ? o.ts_ms : parseMaybeDate(o.submitted_at);

const fillTs = (f: FillExtra): number =>
  typeof f.ts_ms === "number" ? f.ts_ms : parseMaybeDate(f.executed_at);

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

export const useOrders = create<OrdersState>()((set) => ({
  orders: {},
  fills: {},

  setFromSnapshot: ({ orders, fills }) =>
    set((state) => {
      const nextOrders: Record<string, OrderItem[]> = { ...state.orders };
      const nextFills: Record<string, FillItem[]> = { ...state.fills };

      if (orders && orders.length) {
        // group by symbol then merge+dedupe
        const grouped: Record<string, OrderItem[]> = {};
        for (const o of orders) {
          const sym = normSymbol(o.symbol);
          (grouped[sym] ||= []).push(o);
        }
        for (const sym of Object.keys(grouped)) {
          const cur = nextOrders[sym] ?? [];
          const merged = dedupeByIdLatest<OrderItem>(cur, grouped[sym], (x) =>
            orderTs(x as OrderExtra)
          )
            .sort((a, b) => orderTs(b as OrderExtra) - orderTs(a as OrderExtra))
            .slice(0, HISTORY_CAP);
          nextOrders[sym] = merged;
        }
      }

      if (fills && fills.length) {
        const grouped: Record<string, FillItem[]> = {};
        for (const f of fills) {
          const sym = normSymbol(f.symbol);
          (grouped[sym] ||= []).push(f);
        }
        for (const sym of Object.keys(grouped)) {
          const cur = nextFills[sym] ?? [];
          const merged = dedupeByIdLatest<FillItem>(cur, grouped[sym], (x) =>
            fillTs(x as FillExtra)
          )
            .sort((a, b) => fillTs(b as FillExtra) - fillTs(a as FillExtra))
            .slice(0, HISTORY_CAP);
          nextFills[sym] = merged;
        }
      }

      return { orders: nextOrders, fills: nextFills };
    }),
}));
