// src/store/pnl.ts
import { create } from "zustand";

/* ─────────────────────────── Types ─────────────────────────── */

export type Period = "today" | "wtd" | "mtd" | "custom";

export type SummaryResponse = {
  period: Period | string;
  total_usd: number;
  by_exchange: Record<string, unknown>[];
  by_symbol: Record<string, unknown>[];
};

export type SymbolDetailResponse = {
  symbol: string;
  exchange: string;
  account_id: string;
  total_usd: number;
  components: Record<string, unknown>;
  last_events: Record<string, unknown>[];
};

type SummaryParams = {
  period: Period;
  tz: string;
  exchange?: string | null;
  accountId?: string | null;
  fromISO?: string | null; // used only when period === "custom"
  toISO?: string | null;
};

type SymbolParams = Omit<SummaryParams, "fromISO" | "toISO"> & {
  symbol: string;
};

type DetailCacheEntry = {
  data?: SymbolDetailResponse;
  error?: string | null;
  loading: boolean;
  ts?: number; // ms since epoch
};

/* ───────────────────────── Helpers ───────────────────────── */

const browserTZ = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};

const makeSummaryURL = (p: SummaryParams): string => {
  const q = new URLSearchParams();
  q.set("period", p.period);
  if (p.tz) q.set("tz", p.tz);
  if (p.exchange) q.set("exchange", p.exchange);
  if (p.accountId) q.set("account_id", p.accountId);
  if (p.period === "custom") {
    if (p.fromISO) q.set("from", p.fromISO);
    if (p.toISO) q.set("to", p.toISO);
  }
  return `/api/pnl/summary?${q.toString()}`;
};

const makeSymbolURL = (p: SymbolParams): string => {
  const q = new URLSearchParams();
  if (p.exchange) q.set("exchange", p.exchange);
  if (p.accountId) q.set("account_id", p.accountId);
  if (p.period) q.set("period", p.period);
  if (p.tz) q.set("tz", p.tz);
  return `/api/pnl/symbol/${encodeURIComponent(p.symbol)}?${q.toString()}`;
};

// Build a stable cache key per symbol+period+tz+account/exchange
const symbolCacheKey = (p: SymbolParams): string =>
  [
    p.symbol.toUpperCase(),
    p.period,
    p.tz,
    p.exchange || "",
    p.accountId || "",
  ].join("|");

/* ─────────────────────────── Store ─────────────────────────── */

type PnlState = {
  // current controls (used by PositionSummary and default for modal)
  params: SummaryParams;

  // account summary state
  summary: SummaryResponse | null;
  loadingSummary: boolean;
  errorSummary: string | null;

  // per-symbol cache
  detailByKey: Record<string, DetailCacheEntry>;
  detailTtlMs: number; // cache lifetime

  // actions: controls
  setPeriod: (period: Period) => void;
  setTz: (tz: string) => void;
  setCustomRange: (fromISO: string | null, toISO: string | null) => void;
  setExchange: (exchange: string | null) => void;
  setAccountId: (accountId: string | null) => void;

  // actions: fetch
  fetchSummary: () => Promise<void>;
  fetchSymbolDetail: (symbol: string, opts?: { force?: boolean }) => Promise<DetailCacheEntry>;

  // utils
  invalidateSummary: () => void;
  invalidateSymbolDetail: (symbol: string) => void;
};

export const usePnlStore = create<PnlState>((set, get) => ({
  /* defaults */
  params: {
    period: "today",
    tz: browserTZ(),
    exchange: null,
    accountId: null,
    fromISO: null,
    toISO: null,
  },

  summary: null,
  loadingSummary: false,
  errorSummary: null,

  detailByKey: {},
  detailTtlMs: 5_000,

  /* controls */
  setPeriod: (period) => set((s) => ({ params: { ...s.params, period } })),
  setTz: (tz) => set((s) => ({ params: { ...s.params, tz } })),
  setCustomRange: (fromISO, toISO) =>
    set((s) => ({ params: { ...s.params, fromISO, toISO } })),
  setExchange: (exchange) =>
    set((s) => ({ params: { ...s.params, exchange: exchange ?? null } })),
  setAccountId: (accountId) =>
    set((s) => ({ params: { ...s.params, accountId: accountId ?? null } })),

  /* fetch summary */
  fetchSummary: async () => {
    const { params } = get();
    try {
      set({ loadingSummary: true, errorSummary: null });
      const res = await fetch(makeSummaryURL(params), {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`GET /api/pnl/summary failed: ${res.status} ${text}`);
      }
      const data: SummaryResponse = await res.json();
      set({ summary: data });
    } catch (e) {
      set({
        errorSummary: e instanceof Error ? e.message : "Failed to load PnL summary",
      });
    } finally {
      set({ loadingSummary: false });
    }
  },

  /* fetch per-symbol detail (cached) */
  fetchSymbolDetail: async (symbol: string, opts) => {
    const { params, detailByKey, detailTtlMs } = get();
    const key = symbolCacheKey({ ...params, symbol });

    // cache hit within TTL
    const now = Date.now();
    const cached = detailByKey[key];
    if (cached && cached.data && cached.ts && now - cached.ts < detailTtlMs && !opts?.force) {
      return cached;
    }

    // set loading
    set((s) => ({
      detailByKey: {
        ...s.detailByKey,
        [key]: { ...s.detailByKey[key], loading: true, error: null },
      },
    }));

    try {
      const res = await fetch(makeSymbolURL({ ...params, symbol }), {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`GET /api/pnl/symbol/${symbol} failed: ${res.status} ${text}`);
      }
      const data: SymbolDetailResponse = await res.json();
      const entry: DetailCacheEntry = { data, loading: false, error: null, ts: now };
      set((s) => ({
        detailByKey: { ...s.detailByKey, [key]: entry },
      }));
      return entry;
    } catch (e) {
      const entry: DetailCacheEntry = {
        data: undefined,
        loading: false,
        error: e instanceof Error ? e.message : "Failed to load symbol PnL",
        ts: now,
      };
      set((s) => ({
        detailByKey: { ...s.detailByKey, [key]: entry },
      }));
      return entry;
    }
  },

  /* utils */
  invalidateSummary: () => set({ summary: null }),
  invalidateSymbolDetail: (symbol: string) =>
    set((s) => {
      const { params } = s;
      const key = symbolCacheKey({ ...params, symbol });
      const next = { ...s.detailByKey };
      delete next[key];
      return { detailByKey: next };
    }),
}));
