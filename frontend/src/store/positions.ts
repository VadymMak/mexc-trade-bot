// src/store/positions.ts
import { create } from "zustand";

/* ───────────────────────── Types ───────────────────────── */

export type Position = {
  /** Символ, например BTCUSDT */
  symbol: string;
  /** Кол-во базового актива (после flatten может быть 0) */
  qty: number;
  /** Средняя входная цена */
  avg_price?: number;
  /** Реализованный PnL в USDT (наш канонический ключ) */
  realized_pnl?: number;
  /** Доп. поля от бэкенда допустимы */
  account_id?: string;
  exchange?: string;
  [k: string]: unknown;
};

type PositionsBySymbol = Record<string, Position>;

export type Period = "today" | "wtd" | "mtd" | "custom";

type PnlParams = {
  period: Period;
  tz?: string | null;
  from?: string | null; // ISO-8601 (UTC) when period=custom
  to?: string | null;   // ISO-8601 (UTC) when period=custom
};

type PositionsState = {
  positionsBySymbol: PositionsBySymbol;
  loading: boolean;
  error: string | null;

  // Legacy daily summary (kept for backward compatibility)
  dailyRPnL: number | null;
  loadingDaily: boolean;
  errorDaily: string | null;

  // New generic summary (period/TZ aware)
  pnlSummary: number | null;
  pnlLoading: boolean;
  pnlError: string | null;
  pnlParams: PnlParams;
  setPnlParams: (p: Partial<PnlParams>) => void;
  loadPnlSummary: (override?: Partial<PnlParams>) => Promise<void>;

  // CRUD
  setPositions: (list: Position[]) => void;
  upsert: (p: Position) => void;
  remove: (symbol: string) => void;

  // Fetch
  loadAll: (symbols?: string[]) => Promise<void>;
  loadDailyRPnL: () => Promise<void>;

  // Aggregations (только локальные вычисления)
  totalExposureUSD: (getMarkPrice: (symbol: string) => number | undefined) => number;
  totalUPnL: (getMarkPrice: (symbol: string) => number | undefined) => number;
  totalRPnL: () => number;
};

/* ─────────────────────── Helpers ─────────────────────── */

const norm = (s: string): string => (s || "").trim().toUpperCase();

function pickNum<T extends object>(obj: T, key: keyof T): number | undefined {
  const v = obj[key];
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function normalizePosition(p: Position): Position | null {
  const symbol = norm(p.symbol);
  if (!symbol) return null;

  const avg =
    pickNum(p as Record<string, unknown>, "avg_price") ??
    pickNum(p as Record<string, unknown>, "avg");

  const rpnl =
    pickNum(p as Record<string, unknown>, "realized_pnl") ??
    pickNum(p as Record<string, unknown>, "rpnl");

  return {
    ...p,
    symbol,
    ...(avg !== undefined ? { avg_price: avg } : {}),
    ...(rpnl !== undefined ? { realized_pnl: rpnl } : {}),
  };
}

function toMap(list: Position[]): PositionsBySymbol {
  const m: PositionsBySymbol = {};
  for (const raw of list) {
    const n = normalizePosition(raw);
    if (!n) continue;
    m[n.symbol] = n;
  }
  return m;
}

function computeUPnL(p: Position, mark?: number): number {
  if (!Number.isFinite(mark ?? NaN)) return 0;
  if (!Number.isFinite(p.qty)) return 0;
  const avg = pickNum(p as Record<string, unknown>, "avg_price");
  if (!Number.isFinite(avg ?? NaN)) return 0;
  return ((mark as number) - (avg as number)) * p.qty;
}

const defaultPnlParams: PnlParams = {
  period: "today",
  tz: null,
  from: null,
  to: null,
};

/* ──────────────────────── Store ──────────────────────── */

export const usePositionsStore = create<PositionsState>((set, get) => ({
  positionsBySymbol: {},
  loading: false,
  error: null,

  // legacy (kept)
  dailyRPnL: null,
  loadingDaily: false,
  errorDaily: null,

  // new generic summary
  pnlSummary: null,
  pnlLoading: false,
  pnlError: null,
  pnlParams: defaultPnlParams,

  setPnlParams: (p) => {
    const prev = get().pnlParams;
    set({ pnlParams: { ...prev, ...p } });
  },

  loadPnlSummary: async (override) => {
    try {
      const base = get().pnlParams;
      const params: PnlParams = { ...base, ...(override ?? {}) };
      set({ pnlLoading: true, pnlError: null, pnlParams: params });

      const qs = new URLSearchParams();
      qs.set("period", params.period);
      if (params.tz) qs.set("tz", params.tz);
      if (params.period === "custom") {
        if (params.from) qs.set("from", params.from);
        if (params.to) qs.set("to", params.to);
      }

      const res = await fetch(`/api/pnl/summary?${qs.toString()}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`GET /api/pnl/summary failed: ${res.status} ${text}`);
      }
      const data: { total_usd?: number } = await res.json();
      set({ pnlSummary: typeof data?.total_usd === "number" ? data.total_usd : 0 });
    } catch (err) {
      set({
        pnlError: err instanceof Error ? err.message : "Failed to load PnL summary",
      });
    } finally {
      set({ pnlLoading: false });
    }
  },

  setPositions: (list: Position[]) => {
    set({ positionsBySymbol: toMap(list) });
  },

  upsert: (p: Position) => {
    const n = normalizePosition(p);
    if (!n) return;
    const prev = get().positionsBySymbol;
    set({ positionsBySymbol: { ...prev, [n.symbol]: n } });
  },

  remove: (symbol: string) => {
    const sym = norm(symbol);
    const next = { ...get().positionsBySymbol };
    delete next[sym];
    set({ positionsBySymbol: next });
  },

  loadAll: async (symbols?: string[]) => {
    try {
      set({ loading: true, error: null });
      let qs = "";
      if (symbols && symbols.length > 0) {
        const params = new URLSearchParams();
        for (const s of symbols) params.append("symbols", norm(s));
        qs = `?${params.toString()}`;
      }

      const res = await fetch(`/api/exec/positions${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`GET /api/exec/positions failed: ${res.status} ${text}`);
      }

      const data: unknown = await res.json();
      const list = Array.isArray(data) ? (data as Position[]) : [];
      set({ positionsBySymbol: toMap(list) });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to load positions",
      });
    } finally {
      set({ loading: false });
    }
  },

  loadDailyRPnL: async () => {
    try {
      set({ loadingDaily: true, errorDaily: null });
      const res = await fetch(`/api/pnl/summary?period=today`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`GET /api/pnl/summary failed: ${res.status} ${text}`);
      }
      const data: { total_usd: number } = await res.json();
      set({ dailyRPnL: data.total_usd });
    } catch (err) {
      set({
        errorDaily: err instanceof Error ? err.message : "Failed to load daily RPnL",
      });
    } finally {
      set({ loadingDaily: false });
    }
  },

  totalExposureUSD: (getMarkPrice) => {
    const map = get().positionsBySymbol;
    let total = 0;
    for (const sym of Object.keys(map)) {
      const p = map[sym];
      const mark = getMarkPrice(sym);
      if (Number.isFinite(mark ?? NaN) && Number.isFinite(p.qty)) {
        total += Math.abs(p.qty) * (mark as number);
      }
    }
    return total;
  },

  totalUPnL: (getMarkPrice) => {
    const map = get().positionsBySymbol;
    let total = 0;
    for (const sym of Object.keys(map)) {
      total += computeUPnL(map[sym], getMarkPrice(sym));
    }
    return total;
  },

  totalRPnL: () => {
    // оставлено для обратной совместимости — считает из позиций
    const map = get().positionsBySymbol;
    let total = 0;
    for (const p of Object.values(map)) {
      const r =
        pickNum(p as Record<string, unknown>, "realized_pnl") ??
        pickNum(p as Record<string, unknown>, "rpnl");
      if (Number.isFinite(r ?? NaN)) total += r as number;
    }
    return total;
  },
}));

/* ───────────────────── Selectors ───────────────────── */

export const selectPositionsArray = (s: PositionsState): Position[] =>
  Object.values(s.positionsBySymbol);

export const selectActivePositionsArray = (s: PositionsState): Position[] =>
  Object.values(s.positionsBySymbol).filter(
    (p) => Number.isFinite(p.qty) && p.qty !== 0
  );

export const selectBySymbol =
  (symbol: string) =>
  (s: PositionsState): Position | undefined =>
    s.positionsBySymbol[norm(symbol)];
