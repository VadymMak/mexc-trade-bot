// src/store/pnl.ts
import { create } from "zustand";

/* ─────────────────────────── Types ─────────────────────────── */

export type Period = "today" | "wtd" | "mtd" | "custom";

/** Rows inside the summary response */
export type SummaryRowExchange = {
  exchange: string;
  /** total PnL (USD) for this exchange (or exchange+account) */
  total_usd: number;
  /** optional account dimension if backend includes it */
  account_id?: string | null;
  /** forward-compat extras */
  [k: string]: unknown;
};

export type SummaryRowSymbol = {
  symbol: string;
  total_usd: number;
  exchange?: string | null;
  account_id?: string | null;
  [k: string]: unknown;
};

export type SummaryResponse = {
  period: Period | string;
  total_usd: number;
  by_exchange: SummaryRowExchange[];
  by_symbol: SummaryRowSymbol[];
};

/** Details for a specific symbol within a period */
export type SymbolDetailEvent = {
  ts_ms?: number;
  type?: string;
  [k: string]: unknown;
};

export type SymbolDetailResponse = {
  symbol: string;
  exchange: string;
  account_id: string;
  total_usd: number;
  /** sub-components (e.g., realized, fees, funding, etc.). values are usually numbers */
  components: Record<string, unknown>;
  /** recent events that contributed to PnL */
  last_events: SymbolDetailEvent[];
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
  [p.symbol.toUpperCase(), p.period, p.tz, p.exchange || "", p.accountId || ""].join("|");

// Strict number parser
const fnum = (v: unknown, fallback = 0): number => {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
};

// Normalizers (keep shape flexible but ensure required fields are typed)
const normalizeSummary = (raw: unknown): SummaryResponse => {
  const rec = (raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}) as Record<
    string,
    unknown
  >;

  const period = typeof rec.period === "string" ? rec.period : "today";
  const total_usd = fnum(rec.total_usd, 0);

  const by_exchange: SummaryRowExchange[] = Array.isArray(rec.by_exchange)
    ? (rec.by_exchange as unknown[]).map((row): SummaryRowExchange => {
        const r = (row && typeof row === "object" ? (row as Record<string, unknown>) : {}) as Record<
          string,
          unknown
        >;
        return {
          exchange: typeof r.exchange === "string" ? r.exchange : "",
          total_usd: fnum(r.total_usd, 0),
          account_id:
            typeof r.account_id === "string"
              ? r.account_id
              : r.account_id === null
              ? null
              : undefined,
          ...r,
        };
      })
    : [];

  const by_symbol: SummaryRowSymbol[] = Array.isArray(rec.by_symbol)
    ? (rec.by_symbol as unknown[]).map((row): SummaryRowSymbol => {
        const r = (row && typeof row === "object" ? (row as Record<string, unknown>) : {}) as Record<
          string,
          unknown
        >;
        return {
          symbol: typeof r.symbol === "string" ? r.symbol : "",
          total_usd: fnum(r.total_usd, 0),
          exchange: typeof r.exchange === "string" ? r.exchange : undefined,
          account_id:
            typeof r.account_id === "string"
              ? r.account_id
              : r.account_id === null
              ? null
              : undefined,
          ...r,
        };
      })
    : [];

  return { period, total_usd, by_exchange, by_symbol };
};

const normalizeDetail = (raw: unknown): SymbolDetailResponse => {
  const r = (raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}) as Record<
    string,
    unknown
  >;

  const symbol = typeof r.symbol === "string" ? r.symbol : "";
  const exchange = typeof r.exchange === "string" ? r.exchange : "";
  const account_id = typeof r.account_id === "string" ? r.account_id : "";
  const total_usd = fnum(r.total_usd, 0);

  const components: Record<string, unknown> =
    r.components && typeof r.components === "object" ? (r.components as Record<string, unknown>) : {};

  const last_events: SymbolDetailEvent[] = Array.isArray(r.last_events)
    ? (r.last_events as unknown[]).map((e): SymbolDetailEvent => {
        const ev =
          e && typeof e === "object" ? (e as Record<string, unknown>) : ({} as Record<string, unknown>);
        const ts_ms =
          typeof ev.ts_ms === "number" && Number.isFinite(ev.ts_ms)
            ? ev.ts_ms
            : typeof ev.ts === "number" && Number.isFinite(ev.ts)
            ? ev.ts
            : undefined;
        const type = typeof ev.type === "string" ? ev.type : undefined;
        return { ts_ms, type, ...ev };
      })
    : [];

  return { symbol, exchange, account_id, total_usd, components, last_events };
};

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
      const raw = (await res.json()) as unknown;
      const data = normalizeSummary(raw);
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
  fetchSymbolDetail: async (symbol, opts) => {
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
      const raw = (await res.json()) as unknown;
      const data = normalizeDetail(raw);
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
  invalidateSymbolDetail: (symbol) =>
    set((s) => {
      const { params } = s;
      const key = symbolCacheKey({ ...params, symbol });
      const next = { ...s.detailByKey };
      delete next[key];
      return { detailByKey: next };
    }),
}));
