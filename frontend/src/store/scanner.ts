// src/store/scanner.ts
import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  getScannerGateTop,
  getScannerMexcTop,
  getScannerTopAny,
  startStrategy,
} from "@/api/api";
import type { ScannerRow as ApiScannerRow } from "@/types";

/** Доп. поля, которые бэкенд может прислать (explain и уточнения по спреду). */
type BackendOptional = {
  eff_spread_bps?: number;
  eff_spread_bps_taker?: number;
  spread_bps?: number;
  spread_pct?: number;
  reason?: string | null;
  reasons_all?: string[];
  bid_qty?: number;
  ask_qty?: number;
};

/** Расширенная строка для UI (добавляем вычисляемые поля/алиасы). */
export type UiScannerRow = ApiScannerRow & {
  _bps: number;
  _mid: number;
  _minQty: number;
  _notionalNow: number;
  _notionalProxy: number;
  _quote: string;
  _base: string;

  /** Проброс explain */
  reason?: string | null;
  reasons_all?: string[];
};

export type QuoteFilter = "ALL" | "USDT" | "USDC" | "FDUSD" | "BUSD";
export type ExchangeFilter = "gate" | "mexc" | "all";

type ScannerState = {
  rows: UiScannerRow[];
  lastUpdated: number | null;
  loading: boolean;
  error?: string;

  running: boolean;
  exchange: ExchangeFilter;
  quote: QuoteFilter;

  /** Порог по спреду (bps) для клиентского и серверного фильтра */
  minBps: number;
  /** Мин. ликвидность по USD (L1 notional если есть, иначе 24h proxy) */
  minUsd: number;
  limit: number;
  includeStables: boolean;
  excludeLeveraged: boolean;

  /** Флаг для будущего API; если true — отправляем explain=1 */
  explain: boolean;

  intervalMs: number;

  filtered: () => UiScannerRow[];

  setRunning: (v: boolean) => void;
  setExchange: (v: ExchangeFilter) => void;
  setQuote: (q: QuoteFilter) => void;
  setMinBps: (v: number) => void;
  setMinUsd: (v: number) => void;
  setLimit: (v: number) => void;
  setIncludeStables: (v: boolean) => void;
  setExcludeLeveraged: (v: boolean) => void;
  setExplain: (v: boolean) => void;
  setIntervalMs: (v: number) => void;

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

/** Позволяем учитывать bid_qty/ask_qty, если появятся в ответе. */
type MaybeQty = Pick<BackendOptional, "bid_qty" | "ask_qty">;

/** Предпочитаем eff_spread_bps (алиас taker), затем spread_bps, затем пересчёт. */
function pickBestBps(r: ApiScannerRow & Partial<BackendOptional>): number {
  if (typeof r.eff_spread_bps === "number") return r.eff_spread_bps;
  if (typeof r.eff_spread_bps_taker === "number") return r.eff_spread_bps_taker;
  if (typeof r.spread_bps === "number") return r.spread_bps;
  if (typeof r.spread_pct === "number") return r.spread_pct * 100.0; // % → bps
  return calcBps(r.bid, r.ask);
}

function toUiRow(r: ApiScannerRow): UiScannerRow {
  const rOpt = r as ApiScannerRow & Partial<BackendOptional>;

  const spreadBps = pickBestBps(rOpt);

  const mid =
    Number.isFinite(r.bid) && Number.isFinite(r.ask) && r.bid > 0 && r.ask > 0
      ? (r.bid + r.ask) / 2
      : Number.isFinite(r.last)
      ? Number(r.last)
      : 0;

  const withQty = rOpt as ApiScannerRow & MaybeQty;
  const bidQty = typeof withQty.bid_qty === "number" ? withQty.bid_qty : 0;
  const askQty = typeof withQty.ask_qty === "number" ? withQty.ask_qty : 0;
  const minQty = Math.min(bidQty || 0, askQty || 0);
  const notionalNow = mid > 0 && minQty > 0 ? mid * minQty : 0;

  const notionalProxy = typeof r.quote_volume_24h === "number" ? r.quote_volume_24h : 0;

  return {
    ...r,
    _bps: spreadBps,
    _mid: mid,
    _minQty: minQty,
    _notionalNow: notionalNow,
    _notionalProxy: notionalProxy,
    _quote: quoteOf(r.symbol),
    _base: baseOf(r.symbol),
    reason: rOpt.reason ?? undefined,
    reasons_all: Array.isArray(rOpt.reasons_all) ? rOpt.reasons_all : undefined,
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

/* ───────── store ───────── */

export const useScannerStore = create<ScannerState>()(
  devtools((set, get) => ({
    rows: [],
    lastUpdated: null,
    loading: false,
    error: undefined,

    running: true,
    exchange: "gate",
    quote: "USDT",

    minBps: 2.0,
    minUsd: 50_000,
    limit: 200,
    includeStables: false,
    excludeLeveraged: true,

    explain: true,
    intervalMs: 4000,

    filtered: () => {
      const { rows, quote, minBps, minUsd } = get();
      const byQuote = quote === "ALL" ? rows : rows.filter((r) => r._quote === quote);
      const byLiq = byQuote.filter(
        (r) => r._bps >= minBps && (r._notionalNow >= minUsd || r._notionalProxy >= minUsd)
      );
      // чем меньше bps, тем лучше — сортируем по возрастанию
      return [...byLiq].sort((a, b) => a._bps - b._bps);
    },

    setRunning: (v) => set({ running: v }),
    setExchange: (v) => set({ exchange: v }),
    setQuote: (q) => set({ quote: q }),
    setMinBps: (v) => set({ minBps: v }),
    setMinUsd: (v) => set({ minUsd: v }),
    setLimit: (v) => set({ limit: v }),
    setIncludeStables: (v) => set({ includeStables: v }),
    setExcludeLeveraged: (v) => set({ excludeLeveraged: v }),
    setExplain: (v) => set({ explain: v }),
    setIntervalMs: (v) => set({ intervalMs: v }),

    refresh: async () => {
      set({ loading: true, error: undefined });
      try {
        const {
          exchange,
          quote,
          minBps,
          minUsd,
          limit,
          includeStables,
          excludeLeveraged,
          explain,
        } = get();

        let raw: ApiScannerRow[] = [];

        if (exchange === "gate") {
          raw = await getScannerGateTop({
            quote,
            minBps, // server-side filter to reduce payload + flicker
            minUsd,
            limit,
            includeStables,
            excludeLeveraged,
            explain,
          });
        } else if (exchange === "mexc") {
          raw = await getScannerMexcTop({
            quote,
            minBps,
            minUsd,
            limit,
            includeStables,
            excludeLeveraged,
            explain,
          });
        } else {
          // "all"
          raw = await getScannerTopAny("all", {
            quote,
            minBps,
            minUsd,
            limit,
            includeStables,
            excludeLeveraged,
            explain,
          });
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
