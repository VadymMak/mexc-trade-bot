// src/store/scanner.ts
import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  getScannerGateTop,
  getScannerMexcTop,
  getScannerTopAny,
  startStrategy,
} from "@/api/api";
import type { ScannerRow as ApiScannerRow, ScannerUiRow, GetScannerOpts } from "@/types";

export type QuoteFilter = "ALL" | "USDT" | "USDC" | "FDUSD" | "BUSD";
export type ExchangeFilter = "gate" | "mexc" | "all";

/** ───────── store shape ───────── */
type ScannerState = {
  rows: ScannerUiRow[];
  lastUpdated: number | null;
  loading: boolean;
  error?: string;

  running: boolean;
  intervalMs: number;

  exchange: ExchangeFilter;

  // backend-native
  preset: string;
  quote: QuoteFilter;
  fetchCandles: boolean;
  rotation: boolean;
  explain: boolean;
  depthBpsLevels: number[];

  // FE & legacy gates
  minBps: number;
  minUsd: number;
  limit: number;
  includeStables: boolean;
  excludeLeveraged: boolean;

  // extra FE gates aligned to strategy
  minDepth5Usd: number;
  minDepth10Usd: number;
  minTradesPerMin: number;
  hideUnknownFees: boolean;

  filtered: () => ScannerUiRow[];

  // setters
  setRunning: (v: boolean) => void;
  setIntervalMs: (v: number) => void;
  setExchange: (v: ExchangeFilter) => void;

  setPreset: (v: string) => void;
  setQuote: (q: QuoteFilter) => void;
  setFetchCandles: (v: boolean) => void;
  setRotation: (v: boolean) => void;
  setExplain: (v: boolean) => void;
  setDepthBpsLevels: (v: number[]) => void;

  setMinBps: (v: number) => void;
  setMinUsd: (v: number) => void;
  setLimit: (v: number) => void;
  setIncludeStables: (v: boolean) => void;
  setExcludeLeveraged: (v: boolean) => void;

  setMinDepth5Usd: (v: number) => void;
  setMinDepth10Usd: (v: number) => void;
  setMinTradesPerMin: (v: number) => void;
  setHideUnknownFees: (v: boolean) => void;

  // actions
  refresh: () => Promise<void>;
  sendToStrategy: (symbol: string) => Promise<void>;
};

/* ───────── helpers ───────── */

function calcBps(bid?: number, ask?: number): number {
  if (!Number.isFinite(bid) || !Number.isFinite(ask)) return 0;
  const b = Number(bid);
  const a = Number(ask);
  if (b <= 0 || a <= 0 || a <= b) return 0;
  return ((a - b) / ((a + b) * 0.5)) * 10_000;
}

function quoteOf(symbol: string): string {
  const s = symbol.toUpperCase();
  for (const q of ["USDT", "USDC", "FDUSD", "BUSD"] as const) {
    if (s.endsWith(q)) return q;
  }
  return "OTHER";
}

function baseOf(symbol: string): string {
  const s = symbol.toUpperCase();
  for (const q of ["USDT", "USDC", "FDUSD", "BUSD"] as const) {
    if (s.endsWith(q)) return s.slice(0, -q.length);
  }
  return s;
}

function computeMinDepth(
  depthMap?: Record<number, { bid_usd?: number; ask_usd?: number }>
): number | undefined {
  if (!depthMap) return undefined;
  
  const depths: number[] = [];
  for (const [, depth] of Object.entries(depthMap)) {
    if (typeof depth.bid_usd === 'number') depths.push(depth.bid_usd);
    if (typeof depth.ask_usd === 'number') depths.push(depth.ask_usd);
  }
  
  return depths.length > 0 ? Math.min(...depths) : undefined;
}

/* ───────── depth checking helpers ───────── */

/**
 * Check if depth at specific BPS level meets threshold.
 * Returns true if both bid and ask meet threshold.
 * Returns null if depth_at_bps data not available at this level.
 */
function checkDepthAtBps(
  r: ScannerUiRow, 
  bps: number, 
  minUsd: number
): boolean | null {
  const depth = r.depth_at_bps?.[bps];
  if (!depth) return null; // No data at this BPS level
  
  const bidOk = typeof depth.bid_usd === 'number' && depth.bid_usd >= minUsd;
  const askOk = typeof depth.ask_usd === 'number' && depth.ask_usd >= minUsd;
  
  // Both sides must meet threshold
  return bidOk && askOk;
}

/**
 * Fallback: Check legacy depth5 fields.
 * Returns true if depth meets threshold OR if data is missing (permissive).
 */
function checkLegacyDepth5(r: ScannerUiRow, minUsd: number): boolean {
  return (
    r.depth5_bid_usd === undefined ||
    r.depth5_ask_usd === undefined ||
    (r.depth5_bid_usd >= minUsd && r.depth5_ask_usd >= minUsd)
  );
}

/**
 * Fallback: Check legacy depth10 fields.
 */
function checkLegacyDepth10(r: ScannerUiRow, minUsd: number): boolean {
  return (
    r.depth10_bid_usd === undefined ||
    r.depth10_ask_usd === undefined ||
    (r.depth10_bid_usd >= minUsd && r.depth10_ask_usd >= minUsd)
  );
}

function pickBestBps(r: ApiScannerRow): number {
  if (typeof r.eff_spread_maker_bps === "number") return r.eff_spread_maker_bps;
  if (typeof r.eff_spread_taker_bps === "number") return r.eff_spread_taker_bps;
  if (typeof r.spread_bps === "number") return r.spread_bps;
  if (typeof r.spread_pct === "number") return r.spread_pct * 100.0;
  return calcBps(r.bid, r.ask);
}

function toUiRow(r: ApiScannerRow): ScannerUiRow {

  const spreadBps = pickBestBps(r);

  const mid =
    Number.isFinite(r.bid) && Number.isFinite(r.ask) && r.bid > 0 && r.ask > 0
      ? (r.bid + r.ask) / 2
      : Number.isFinite(r.last)
      ? Number(r.last)
      : 0;

  const notionalNow = 0;

  const notionalProxy = typeof r.quote_volume_24h === "number" ? r.quote_volume_24h : 0;

  const reasons = Array.isArray(r.reasons_all) ? r.reasons_all : [];
  const feeUnknown = reasons.includes("fees:none");

  const minDepthAtBps = computeMinDepth(r.depth_at_bps);

  return {
  ...r,
  // Internal computed fields (prefixed with _)
  _bps: spreadBps,
  _mid: mid,
  _minQty: 0,
  _notionalNow: notionalNow,
  _notionalProxy: notionalProxy,
  _quote: quoteOf(r.symbol),
  _base: baseOf(r.symbol),
  _feeUnknown: feeUnknown,
  _minDepthAtBps: minDepthAtBps,
  
  // Page-level computed fields (no prefix) - computed here for compatibility
  mid: mid,
  spread_bps_ui: spreadBps,
  daily_notional_usd: notionalProxy,
  quote_ccy: quoteOf(r.symbol),
  base_ccy: baseOf(r.symbol),
  fee_unknown: feeUnknown,
};
}

function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

/* ───────── persistence (localStorage) ───────── */

const LS_KEY = "scanner.store.filters.v1";

// Only persist non-function, user-controlled filters (avoid rows, errors, etc.)
type PersistSlice = Pick<
  ScannerState,
  | "running"
  | "intervalMs"
  | "exchange"
  | "preset"
  | "quote"
  | "fetchCandles"
  | "rotation"
  | "explain"
  | "depthBpsLevels"
  | "minBps"
  | "minUsd"
  | "limit"
  | "includeStables"
  | "excludeLeveraged"
  | "minDepth5Usd"
  | "minDepth10Usd"
  | "minTradesPerMin"
  | "hideUnknownFees"
>;

function loadPersist(): Partial<PersistSlice> {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<PersistSlice>;
    return parsed ?? {};
  } catch {
    return {};
  }
}
function savePersist(s: PersistSlice) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(s));
  } catch {
    /* ignore quota errors */
  }
}
function pickPersist(s: ScannerState): PersistSlice {
  const snap: PersistSlice = {
    running: s.running,
    intervalMs: s.intervalMs,
    exchange: s.exchange,
    preset: s.preset,
    quote: s.quote,
    fetchCandles: s.fetchCandles,
    rotation: s.rotation,
    explain: s.explain,
    depthBpsLevels: s.depthBpsLevels,
    minBps: s.minBps,
    minUsd: s.minUsd,
    limit: s.limit,
    includeStables: s.includeStables,
    excludeLeveraged: s.excludeLeveraged,
    minDepth5Usd: s.minDepth5Usd,
    minDepth10Usd: s.minDepth10Usd,
    minTradesPerMin: s.minTradesPerMin,
    hideUnknownFees: s.hideUnknownFees,
  };
  return snap;
}

/* ───────── store ───────── */

export const useScannerStore = create<ScannerState>()(
  devtools((set, get) => ({
    rows: [],
    lastUpdated: null,
    loading: false,
    error: undefined,

    running: true,
    intervalMs: 4000,

    exchange: "gate",

    // backend-native defaults
    preset: "hedgehog",
    quote: "USDT",
    fetchCandles: true,
    rotation: true,
    explain: true,
    depthBpsLevels: [5, 10],

    // FE & legacy gates
    minBps: 10,
    minUsd: 10_000,
    limit: 100,
    includeStables: false,
    excludeLeveraged: true,

    // extra FE gates aligned to strategy
    minDepth5Usd: 1_000,
    minDepth10Usd: 3_000,
    minTradesPerMin: 5,
    hideUnknownFees: true,

    filtered: () => {
      const {
        rows,
        quote,
        minBps,
        minUsd,
        minDepth5Usd,
        minDepth10Usd,
        minTradesPerMin,
        hideUnknownFees,
      } = get();

      let list = quote === "ALL" ? rows : rows.filter((r) => r._quote === quote);
      if (hideUnknownFees) list = list.filter((r) => !r._feeUnknown);

      list = list.filter((r) => {
        const bpsOK = r._bps >= minBps;
        const usdOK = (r._notionalNow || 0) >= minUsd || (r._notionalProxy || 0) >= minUsd;

        const depth5OK = checkDepthAtBps(r, 5, minDepth5Usd) ?? 
                     checkLegacyDepth5(r, minDepth5Usd);
    
    const depth10OK = checkDepthAtBps(r, 10, minDepth10Usd) ?? 
                      checkLegacyDepth10(r, minDepth10Usd);

        const tpmOK =
          typeof r.trades_per_min !== "number" || r.trades_per_min >= minTradesPerMin;

        return bpsOK && usdOK && depth5OK && depth10OK && tpmOK;
      });

      return [...list].sort((a, b) => b._bps - a._bps);
    },

    // setters
    setRunning: (v) => set({ running: v }),
    setIntervalMs: (v) => set({ intervalMs: v }),
    setExchange: (v) => set({ exchange: v }),

    setPreset: (v) => set({ preset: v }),
    setQuote: (q) => set({ quote: q }),
    setFetchCandles: (v) => set({ fetchCandles: v }),
    setRotation: (v) => set({ rotation: v }),
    setExplain: (v) => set({ explain: v }),
    setDepthBpsLevels: (v) => set({ depthBpsLevels: v && v.length ? v : [5, 10] }),

    setMinBps: (v) => set({ minBps: v }),
    setMinUsd: (v) => set({ minUsd: v }),
    setLimit: (v) => set({ limit: v }),
    setIncludeStables: (v) => set({ includeStables: v }),
    setExcludeLeveraged: (v) => set({ excludeLeveraged: v }),

    setMinDepth5Usd: (v) => set({ minDepth5Usd: v }),
    setMinDepth10Usd: (v) => set({ minDepth10Usd: v }),
    setMinTradesPerMin: (v) => set({ minTradesPerMin: v }),
    setHideUnknownFees: (v) => set({ hideUnknownFees: v }),

    refresh: async () => {
      set({ loading: true, error: undefined });
      try {
        const {
          exchange,
          preset,
          quote,
          fetchCandles,
          rotation,
          explain,
          depthBpsLevels,

          minBps,
          minUsd,
          limit,
          includeStables,
          excludeLeveraged,

          minDepth5Usd,
          minDepth10Usd,
          minTradesPerMin,
        } = get();

        const quoteParam: string = quote === "ALL" ? "USDT" : quote;

        // Use snake_case names expected by GetScannerOpts; api.ts maps minBps → min_spread_pct
        const baseOpts: GetScannerOpts = {
          preset,
          quote: quoteParam,
          fetch_candles: fetchCandles,
          depth_bps_levels: depthBpsLevels,
          rotation,
          explain,
          limit,

          minBps,
          min_quote_vol_usd: minUsd,
          include_stables: includeStables,
          exclude_leveraged: excludeLeveraged,
          min_depth5_usd: minDepth5Usd,
          min_depth10_usd: minDepth10Usd,
          min_trades_per_min: minTradesPerMin,
        };

        let raw: ApiScannerRow[] = [];

        if (exchange === "gate") {
          raw = await getScannerGateTop(baseOpts);
        } else if (exchange === "mexc") {
          raw = await getScannerMexcTop(baseOpts);
        } else {
          raw = await getScannerTopAny("all", baseOpts);
        }

        set({
          rows: raw.map(toUiRow),
          lastUpdated: Date.now(),
          loading: false,
        });
      } catch (e: unknown) {
        set({ loading: false, error: errorMessage(e) });
      }
    },

    sendToStrategy: async (symbol: string) => {
      await startStrategy(symbol);
    },
  }))
);

/** Hydrate from localStorage (once) */
(() => {
  const saved = loadPersist();
  if (saved && Object.keys(saved).length) {
    useScannerStore.setState(saved as Partial<ScannerState>, false, "scanner/hydrate");
  }
})();

/** Persist to localStorage on every relevant change (no selector overload) */
useScannerStore.subscribe((s: ScannerState) => {
  savePersist(pickPersist(s));
});

