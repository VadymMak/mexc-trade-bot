// src/pages/LiquidityScanner.tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiStartSymbols } from "@/api/api";
import type {
  ScannerUiRow,
  QuoteFilter,
  ExchangeFilter,
  Preset,
} from "@/types";
import { useInterval } from "@/hooks/useInterval";
import { formatNumber } from "@/utils/format";
import { useScannerStore } from "@/store/scanner";
import PageToolbar from "@/components/layout/PageToolbar";

/* ───────── helpers ───────── */
function quoteOf(symbol: string): string {
  const s = symbol.toUpperCase();
  for (const q of ["USDT", "USDC", "FDUSD", "BUSD"]) if (s.endsWith(q)) return q;
  return "OTHER";
}
function baseOf(symbol: string): string {
  const s = symbol.toUpperCase();
  for (const q of ["USDT", "USDC", "FDUSD", "BUSD"])
    if (s.endsWith(q)) return s.slice(0, -q.length);
  return s;
}
function toVenueLink(exchange: ExchangeFilter, sym: string): string {
  const base = baseOf(sym);
  const quote = quoteOf(sym);
  if (exchange === "mexc") return `https://www.mexc.com/exchange/${base}_${quote}`;
  return `https://www.gate.io/trade/${base}_${quote}`;
}
function parseDepthLevelsCsv(csv: string): number[] {
  return (csv || "")
    .split(",")
    .map((x) => Number(String(x).trim()))
    .filter((n) => Number.isFinite(n) && n > 0);
}

/**
 * Assess depth quality based on minimum side vs threshold
 * Returns: 'strong' | 'moderate' | 'weak'
 */
function getDepthQuality(bidUsd: number, askUsd: number, threshold: number): 'strong' | 'moderate' | 'weak' {
  const minSide = Math.min(bidUsd, askUsd);
  if (minSide >= threshold * 3) return 'strong';
  if (minSide >= threshold) return 'moderate';
  return 'weak';
}

function fmtUpdated(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleTimeString();
}

/* ───────── DepthCell Component ───────── */
interface DepthCellProps {
  bps: number;
  bidUsd?: number;
  askUsd?: number;
  threshold: number;
}

function DepthCell({  bidUsd = 0, askUsd = 0, threshold }: DepthCellProps) {
  const quality = getDepthQuality(bidUsd, askUsd, threshold);
  const minSide = Math.min(bidUsd, askUsd);

  const qualityColor = {
    strong: 'bg-emerald-900/30 border-emerald-700/40 text-emerald-300',
    moderate: 'bg-amber-900/30 border-amber-700/40 text-amber-300',
    weak: 'bg-rose-900/30 border-rose-700/40 text-rose-300',
  }[quality];

  return (
    <div className="space-y-1">
      {/* Min side indicator */}
      <div className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs ${qualityColor}`}>
        <span className="font-medium">{formatNumber(minSide, 0)}</span>
        <span className="text-[10px] opacity-70">min</span>
      </div>

      {/* Bid/Ask breakdown */}
      <div className="text-xs space-y-0.5">
        <div className="flex justify-between gap-2">
          <span className="text-zinc-500">bid:</span>
          <span className="text-emerald-400">{formatNumber(bidUsd, 0)}</span>
        </div>
        <div className="flex justify-between gap-2">
          <span className="text-zinc-500">ask:</span>
          <span className="text-rose-400">{formatNumber(askUsd, 0)}</span>
        </div>
      </div>
    </div>
  );
}

/* ───────── DepthTooltip Component ───────── */
interface DepthTooltipProps {
  depthMap?: Record<number, { bid_usd?: number; ask_usd?: number }>;
  showBelow?: boolean;
}

function DepthTooltip({ depthMap, showBelow = false }: DepthTooltipProps) {
  if (!depthMap || Object.keys(depthMap).length === 0) {
    return (
      <div className="text-xs text-zinc-400">
        No depth data available
      </div>
    );
  }

  const entries = Object.entries(depthMap).sort(([a], [b]) => Number(a) - Number(b));

  return (
    <div className={`text-xs space-y-2 min-w-[220px] max-w-[280px] ${showBelow ? 'mt-2' : 'mb-2'}`}>
      <div className="font-medium text-zinc-300 border-b border-zinc-700 pb-1">
        Depth at all levels
      </div>
      {entries.map(([bps, depth]) => {
        const minSide = Math.min(depth.bid_usd ?? 0, depth.ask_usd ?? 0);
        return (
          <div key={bps} className="space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-zinc-400">@{bps} bps:</span>
              <span className="font-medium text-zinc-200">
                ${formatNumber(minSide, 0)} min
              </span>
            </div>
            <div className="flex justify-between text-[10px] pl-2">
              <span className="text-emerald-400">
                {formatNumber(depth.bid_usd ?? 0, 0)} bid
              </span>
              <span className="text-rose-400">
                {formatNumber(depth.ask_usd ?? 0, 0)} ask
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ───────── page ───────── */
export default function LiquidityScanner() {
  // ═══════════════════════════════════════════════════════
  // 🔄 Zustand store hooks (will gradually replace local state)
  // ═══════════════════════════════════════════════════════
  // Data & loading state
  const storeRows = useScannerStore((s) => s.rows);
  const storeLoading = useScannerStore((s) => s.loading);
  const storeError = useScannerStore((s) => s.error);
  const storeLastUpdated = useScannerStore((s) => s.lastUpdated);
  
  // Filters (persisted in store)
  const storeExchange = useScannerStore((s) => s.exchange);
  const storePreset = useScannerStore((s) => s.preset);
  const storeQuote = useScannerStore((s) => s.quote);
  const storeDepthBpsLevels = useScannerStore((s) => s.depthBpsLevels);
  const storeFetchCandles = useScannerStore((s) => s.fetchCandles);
  const storeRotation = useScannerStore((s) => s.rotation);
  const storeExplain = useScannerStore((s) => s.explain);
  const storeMinBps = useScannerStore((s) => s.minBps);
  const storeMinUsd = useScannerStore((s) => s.minUsd);
  const storeMinDepth5 = useScannerStore((s) => s.minDepth5Usd);
  const storeMinDepth10 = useScannerStore((s) => s.minDepth10Usd);
  const storeMinTradesPerMin = useScannerStore((s) => s.minTradesPerMin);
  const storeLimit = useScannerStore((s) => s.limit);
  const storeIncludeStables = useScannerStore((s) => s.includeStables);
  const storeExcludeLeveraged = useScannerStore((s) => s.excludeLeveraged);
  const storeHideUnknownFees = useScannerStore((s) => s.hideUnknownFees);
  
  // Computed
  // const storeFiltered = useScannerStore((s) => s.filtered);
  
  // Actions
const storeRefresh = useScannerStore((s) => s.refresh);
const storeSetExchange = useScannerStore((s) => s.setExchange);
const storeSetPreset = useScannerStore((s) => s.setPreset);
const storeSetQuote = useScannerStore((s) => s.setQuote);
const storeSetDepthBpsLevels = useScannerStore((s) => s.setDepthBpsLevels);
const storeSetFetchCandles = useScannerStore((s) => s.setFetchCandles);
const storeSetRotation = useScannerStore((s) => s.setRotation);
const storeSetExplain = useScannerStore((s) => s.setExplain);
const storeSetMinBps = useScannerStore((s) => s.setMinBps);
const storeSetMinUsd = useScannerStore((s) => s.setMinUsd);
const storeSetMinDepth5Usd = useScannerStore((s) => s.setMinDepth5Usd);
const storeSetMinDepth10Usd = useScannerStore((s) => s.setMinDepth10Usd);
const storeSetMinTradesPerMin = useScannerStore((s) => s.setMinTradesPerMin);
const storeSetLimit = useScannerStore((s) => s.setLimit);
const storeSetIncludeStables = useScannerStore((s) => s.setIncludeStables);
const storeSetExcludeLeveraged = useScannerStore((s) => s.setExcludeLeveraged);
const storeSetHideUnknownFees = useScannerStore((s) => s.setHideUnknownFees);

  // sorting
  const [sortBy, setSortBy] = useState<keyof ScannerUiRow>("spread_bps_ui");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");


  /// Aliases for cleaner code (point to store values)
  const exchange = storeExchange;
  const preset = storePreset;
  const quote = storeQuote;
  const fetchCandles = storeFetchCandles;
  const rotation = storeRotation;
  const explain = storeExplain;
  const minBps = storeMinBps;
  const minUsdDay = storeMinUsd; // Note: store calls it minUsd
  const minDepth5 = storeMinDepth5;
  const minDepth10 = storeMinDepth10;
  const minTradesPerMin = storeMinTradesPerMin;
  const limit = storeLimit;
  const includeStables = storeIncludeStables;
  const excludeLeveraged = storeExcludeLeveraged;
  const hideUnknownFees = storeHideUnknownFees;
  const [running, setRunning] = useState(true);
  const intervalMs = 4000;

  // Parse depth levels from CSV
  const depthLevelsCsv = storeDepthBpsLevels.join(',');

  // Parse depth levels for table rendering
  const depthLevels = useMemo(() => storeDepthBpsLevels, [storeDepthBpsLevels]);

  /* ───────── data fetch ───────── */
  const fetchRows = useCallback(async () => {
    await storeRefresh();
  }, [storeRefresh]);

  useEffect(() => {
    void fetchRows();
  }, [fetchRows]);

  useInterval(
    () => {
      if (running) void fetchRows();
    },
    running ? Math.max(1000, intervalMs) : null
  );

  /* ───────── client derivations → ScannerUiRow ───────── */
  /* ───────── client derivations → ScannerUiRow ───────── */
  const data: ScannerUiRow[] = useMemo(() => {
    // Store already transforms to ScannerUiRow, just filter if needed
    return storeRows.filter(
      (r) => r.symbol !== "__error__" && (!hideUnknownFees || !r.fee_unknown)
    );
  }, [storeRows, hideUnknownFees]);

  /* ───────── sorting ───────── */
  const sortedData = useMemo(() => {
    return [...data].sort((a, b) => {
      const aVal = Number(a[sortBy] ?? 0);
      const bVal = Number(b[sortBy] ?? 0);
      return sortDir === "desc" ? bVal - aVal : aVal - bVal;
    });
  }, [data, sortBy, sortDir]);

  const handleSort = useCallback((column: keyof ScannerUiRow) => {
    if (sortBy === column) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortBy(column);
      setSortDir("desc");
    }
  }, [sortBy]);

  /* ───────── actions ───────── */
  const onStartAll = useCallback(async () => {
    try {
      const symbols = sortedData.map((r) => r.symbol);
      if (symbols.length === 0) return;
      await apiStartSymbols(symbols);
      alert(`Sent ${symbols.length} symbol(s) to strategy`);
    } catch (e) {
      console.error(e);
      alert("Failed to start some symbols");
    }
  }, [sortedData]);

  const copySymbols = useCallback(async () => {
    const list = sortedData.map((r) => r.symbol).join("\n");
    try {
      await navigator.clipboard.writeText(list);
      alert("Symbols copied to clipboard");
    } catch {
      alert(list);
    }
  }, [sortedData]);

  const openOnVenue = useCallback(() => {
    const ex: ExchangeFilter = exchange;
    sortedData.slice(0, 10).forEach((r) => window.open(toVenueLink(ex, r.symbol), "_blank"));
  }, [sortedData, exchange]);

  const exportCSV = useCallback(() => {
    const headers = ['Symbol', 'Mid', 'Spread (bps)', ...depthLevels.map(b => `Depth@${b}bps`), '$/min', 'Daily $'];
    const csvRows = sortedData.map(r => {
      const depthValues = depthLevels.map(bps => {
        const depth = r.depth_at_bps?.[bps];
        return depth ? Math.min(depth.bid_usd ?? 0, depth.ask_usd ?? 0) : 0;
      });
      return [
        r.symbol,
        r.mid,
        r.spread_bps_ui,
        ...depthValues,
        r.usd_per_min ?? 0,
        r.daily_notional_usd,
      ].join(',');
    });

    const csv = [headers.join(','), ...csvRows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `scanner_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [sortedData, depthLevels]);

  /* ───────── render helper ───────── */
  const SortableHeader = ({ column, children }: { column: keyof ScannerUiRow; children: React.ReactNode }) => (
    <th
      className="px-4 py-3 text-right cursor-pointer hover:bg-zinc-800/50 transition-colors"
      onClick={() => handleSort(column)}
    >
      <div className="flex items-center justify-end gap-1">
        {children}
        {sortBy === column && (
          <span className="text-xs text-zinc-400">
            {sortDir === "desc" ? "↓" : "↑"}
          </span>
        )}
      </div>
    </th>
  );

  /* ───────── render ───────── */
  return (
    <main className="min-h-screen">
      <div className="mx-auto w-full max-w-[1800px] px-6 lg:px-10 py-6 space-y-6">
        {/* header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl md:text-2xl font-semibold">Liquidity Scanner</h1>
            <p className="text-zinc-400 text-sm">Фильтры и отбор ликвидных пар для стратегии.</p>
          </div>

          <div className="flex items-center gap-2">
            <PageToolbar />
            
            {/* Live status indicator */}
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-zinc-900/60 border border-zinc-800">
              <div className={`w-2 h-2 rounded-full ${
                running ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-600'
              }`} />
              <span className="text-sm text-zinc-400">
                {running ? 'Live' : 'Paused'}
              </span>
             {storeLoading && <span className="text-xs text-zinc-500">(updating...)</span>}
            </div>

            <div className="mx-1 h-6 w-px bg-zinc-700/60" />
            
            <button
              className="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 disabled:opacity-60 transition-colors"
              onClick={fetchRows}
              disabled={storeLoading}
              type="button"
            >
              {storeLoading ? "Loading…" : "Refresh"}
            </button>
            <button
              className={`px-3 py-1.5 rounded-xl transition-colors ${
                running ? "bg-emerald-700 hover:bg-emerald-600" : "bg-zinc-800 hover:bg-zinc-700"
              }`}
              onClick={() => setRunning((v) => !v)}
              title={running ? "Stop auto-refresh" : "Start auto-refresh"}
              type="button"
            >
              {running ? "Stop" : "Start"}
            </button>
          </div>
        </div>

        {/* optional venue error banner */}
        {storeError && (
          <div className="rounded-xl border border-amber-600/40 bg-amber-900/20 px-4 py-3 text-amber-200">
            {storeError}
          </div>
        )}

        {/* filters */}
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/20 p-4 md:p-5">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-12 gap-3 md:gap-4 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Exchange</label>
              <select
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10"
                value={exchange}
                onChange={(e) => storeSetExchange(e.target.value as ExchangeFilter)}
              >
                <option value="gate">gate</option>
                <option value="mexc">mexc</option>
                <option value="all">all</option>
              </select>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Preset</label>
              <select
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10"
                value={preset}
                onChange={(e) => storeSetPreset(e.target.value as Preset)}
              >
                <option value="metaskalp">Metaskalp</option>
                <option value="hedgehog">Hedgehog</option>
                <option value="balanced">Balanced</option>
                <option value="scalper">Scalper</option>
                <option value="ерш">ERШ</option>
                <option value="conservative">Conservative</option>
                <option value="aggressive">Aggressive</option>
              </select>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Quote</label>
              <select
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10"
                value={quote}
                onChange={(e) => storeSetQuote(e.target.value as QuoteFilter)}
              >
                <option value="ALL">ALL</option>
                <option value="USDT">USDT</option>
                <option value="USDC">USDC</option>
                <option value="FDUSD">FDUSD</option>
                <option value="BUSD">BUSD</option>
              </select>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Depth levels (bps, CSV)</label>
              <input
                type="text"
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10"
                value={depthLevelsCsv}
                onChange={(e) => {
                  const parsed = parseDepthLevelsCsv(e.target.value);
                  if (parsed.length > 0) {
                    storeSetDepthBpsLevels(parsed);
                    // Auto-refresh after 500ms of no typing
                    setTimeout(() => void storeRefresh(), 500);
                  }
                }}
                placeholder="5,10,15,20"
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Min spread (bps)</label>
              <input
                type="number"
                step="1"
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10 text-right"
                value={minBps}
                onChange={(e) => storeSetMinBps(Number(e.target.value))}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Min $/day</label>
              <input
                type="number"
                step="100"
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10 text-right"
                value={minUsdDay}
                onChange={(e) => storeSetMinUsd(Number(e.target.value))}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Min depth @5bps (USD)</label>
              <input
                type="number"
                step="100"
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10 text-right"
                value={minDepth5}
                onChange={(e) => storeSetMinDepth5Usd(Number(e.target.value))}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Min depth @10bps (USD)</label>
              <input
                type="number"
                step="100"
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10 text-right"
                value={minDepth10}
                onChange={(e) => storeSetMinDepth10Usd(Number(e.target.value))}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Min trades/min</label>
              <input
                type="number"
                step="1"
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10 text-right"
                value={minTradesPerMin}
                onChange={(e) => storeSetMinTradesPerMin(Number(e.target.value))}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-zinc-400 h-4">Limit</label>
              <input
                type="number"
                step="10"
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 h-10 text-right"
                value={limit}
                onChange={(e) => storeSetLimit(Number(e.target.value))}
              />
            </div>

            <div className="flex flex-col justify-end">
              <div className="flex flex-wrap items-center gap-4">
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={includeStables}
                    onChange={(e) => storeSetIncludeStables(e.target.checked)}
                  />
                  <span>Include stables</span>
                </label>
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={excludeLeveraged}
                    onChange={(e) => storeSetExcludeLeveraged(e.target.checked)}
                  />
                  <span>Exclude leveraged</span>
                </label>
              </div>
            </div>

            <div className="flex flex-col justify-end">
              <div className="flex flex-wrap items-center gap-4">
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={fetchCandles}
                    onChange={(e) => storeSetFetchCandles(e.target.checked)}
                  />
                  <span>Fetch candles</span>
                </label>
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={rotation}
                    onChange={(e) => storeSetRotation(e.target.checked)}
                  />
                  <span>Rotation</span>
                </label>
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={explain}
                    onChange={(e) => storeSetExplain(e.target.checked)}
                  />
                  <span>Explain</span>
                </label>
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={hideUnknownFees}
                    onChange={(e) => storeSetHideUnknownFees(e.target.checked)}
                  />
                  <span>Hide unknown fees</span>
                </label>
              </div>
            </div>
          </div>
        </div>

        {/* actions row */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-sm text-zinc-400">
            Found: <span className="text-zinc-200 font-medium">{sortedData.length}</span>
          </div>
          <div className="text-sm text-zinc-500">
            Updated: <span className="text-zinc-300">{fmtUpdated(storeLastUpdated)}</span>
          </div>
          <div className="mx-2 h-6 w-px bg-zinc-700/60" />
          <button
            className="px-3 py-1.5 rounded-xl bg-emerald-700 hover:bg-emerald-600 transition-colors"
            onClick={onStartAll}
            type="button"
            disabled={sortedData.length === 0}
          >
            Send all to Strategy
          </button>
          <button
            className="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 transition-colors"
            onClick={copySymbols}
            type="button"
            disabled={sortedData.length === 0}
          >
            Copy Symbols
          </button>
          <button
            className="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 transition-colors"
            onClick={exportCSV}
            type="button"
            disabled={sortedData.length === 0}
          >
            Export CSV
          </button>
          <button
            className="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 transition-colors"
            onClick={openOnVenue}
            type="button"
            disabled={sortedData.length === 0}
          >
            Open Top 10 on {exchange === "all" ? "Gate" : exchange}
          </button>
        </div>

        {/* table */}
        {/* table */}
        <div className="overflow-x-auto rounded-2xl border border-zinc-800 bg-zinc-900/20 shadow-sm">
          <div className="relative isolate">
            <table className="min-w-full text-sm">
              <thead className="bg-zinc-900/60 sticky top-0 z-10">
                <tr className="text-left">
                <th className="px-4 py-3">Symbol</th>
                <SortableHeader column="mid">Mid</SortableHeader>
                <SortableHeader column="bid">Bid</SortableHeader>
                <SortableHeader column="ask">Ask</SortableHeader>
                <SortableHeader column="spread_bps_ui">Spread (bps)</SortableHeader>
                
                {/* Dynamic depth columns based on depthLevelsCsv */}
                {depthLevels.map((bps) => (
                  <th key={bps} className="px-4 py-3 text-right">
                    <div className="flex flex-col items-end gap-1">
                      <span>Depth@{bps}bps</span>
                      <span className="text-[10px] text-zinc-500">min side</span>
                    </div>
                  </th>
                ))}

                <SortableHeader column="trades_per_min">Trades/min</SortableHeader>
                <SortableHeader column="usd_per_min">$ / min</SortableHeader>
                <SortableHeader column="median_trade_usd">Median trade</SortableHeader>
                <SortableHeader column="daily_notional_usd">Daily $</SortableHeader>
                <SortableHeader column="imbalance">Imbalance</SortableHeader>
                <th className="px-4 py-3 text-right">WS lag, ms</th>
                <th className="px-4 py-3 text-right">Links</th>
              </tr>
            </thead>
            <tbody>
              {sortedData.map((r, rowIndex) => {
                // Determine thresholds for depth quality (use first level as baseline)
                const baseThreshold = depthLevels[0] === 5 ? minDepth5 : 
                                     depthLevels[0] === 10 ? minDepth10 : 1000;
                
                // Show tooltip below for first few rows (near top of table)
                const showTooltipBelow = rowIndex < 3;

                return (
                  <tr key={r.symbol} className="border-t border-zinc-800 hover:bg-zinc-900/30 transition-colors">
                    {/* Symbol column with badges */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">
                          {baseOf(r.symbol)}/{quoteOf(r.symbol)}
                        </span>
                        
                        {/* Exchange badge */}
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800/60 text-zinc-400">
                          {r.exchange || exchange}
                        </span>

                        {/* Fee unknown badge */}
                        {r.fee_unknown && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/30 border border-amber-700/30 text-amber-300">
                            fee:unknown
                          </span>
                        )}

                        {/* Tier badge (if available from tiered endpoint) */}
                        {r.tier && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                            r.tier === 'A' ? 'bg-emerald-900/30 border-emerald-700/30 text-emerald-300' :
                            r.tier === 'B' ? 'bg-amber-900/30 border-amber-700/30 text-amber-300' :
                            'bg-zinc-800 text-zinc-400'
                          }`}>
                            Tier {r.tier}
                          </span>
                        )}
                      </div>
                    </td>

                    <td className="px-4 py-3 text-right">{formatNumber(r.mid, 6)}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(r.bid, 6)}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(r.ask, 6)}</td>
                    
                    {/* Spread with effective indicator */}
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <span>{formatNumber(r.spread_bps_ui, 2)}</span>
                        {(typeof r.eff_spread_bps_maker === "number" ||
                          typeof r.eff_spread_maker_bps === "number") && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/20 text-emerald-400">
                            eff
                          </span>
                        )}
                      </div>
                    </td>

                    {/* Dynamic depth columns with quality indicators */}
                    {depthLevels.map((bps) => {
                      const depth = r.depth_at_bps?.[bps];
                      const threshold = bps === 5 ? minDepth5 : bps === 10 ? minDepth10 : baseThreshold;

                      return (
                        <td key={bps} className="px-4 py-3 relative z-10">
                          <div 
                            className="group cursor-help"
                            title="Hover for full depth breakdown"
                          >
                            {depth ? (
                              <DepthCell
                                bps={bps}
                                bidUsd={depth.bid_usd}
                                askUsd={depth.ask_usd}
                                threshold={threshold}
                              />
                            ) : (
                              // Fallback to legacy fields
                              bps === 5 && (r.depth5_bid_usd || r.depth5_ask_usd) ? (
                                <DepthCell
                                  bps={5}
                                  bidUsd={r.depth5_bid_usd}
                                  askUsd={r.depth5_ask_usd}
                                  threshold={minDepth5}
                                />
                              ) : bps === 10 && (r.depth10_bid_usd || r.depth10_ask_usd) ? (
                                <DepthCell
                                  bps={10}
                                  bidUsd={r.depth10_bid_usd}
                                  askUsd={r.depth10_ask_usd}
                                  threshold={minDepth10}
                                />
                              ) : (
                                <span className="text-zinc-500 text-xs">—</span>
                              )
                            )}

                            {/* Tooltip - positioned dynamically (above or below) */}
                            {r.depth_at_bps && Object.keys(r.depth_at_bps).length > 0 && (
                              <div className={`fixed hidden group-hover:block z-[9999]`}
                                style={{
                                  left: '50%',
                                  transform: 'translateX(-50%)',
                                  [showTooltipBelow ? 'top-full mt-4' : 'bottom']: showTooltipBelow ? 
                                    'calc(var(--cell-bottom, 0px) + 8px)' : 
                                    'calc(100vh - var(--cell-top, 0px) + 8px)'
                                }}
                              >
                                <div className="bg-zinc-900 border-2 border-zinc-700 rounded-lg p-3 shadow-2xl">
                                  <DepthTooltip depthMap={r.depth_at_bps} showBelow={showTooltipBelow} />
                                  {/* Arrow pointer */}
                                  <div className={`fixed hidden group-hover:block z-[9999]`}
                                    onMouseEnter={(e) => {
                                      const rect = e.currentTarget.previousElementSibling?.getBoundingClientRect();
                                      if (rect) {
                                        e.currentTarget.style.left = `${rect.left + rect.width / 2}px`;
                                        e.currentTarget.style.top = showTooltipBelow ? 
                                          `${rect.bottom + 8}px` : 
                                          `${rect.top - e.currentTarget.offsetHeight - 8}px`;
                                      }
                                    }}
                                  >
                                    <div className={`border-8 border-transparent ${
                                      showTooltipBelow 
                                        ? 'border-b-zinc-700' 
                                        : 'border-t-zinc-700'
                                    }`}></div>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        </td>
                      );
                    })}

                    <td className="px-4 py-3 text-right">
                      {typeof r.trades_per_min === 'number' ? formatNumber(r.trades_per_min, 1) : '—'}
                    </td>
                    <td className="px-4 py-3 text-right">{formatNumber(r.usd_per_min ?? 0, 0)}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(r.median_trade_usd ?? 0, 0)}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(r.daily_notional_usd, 0)}</td>
                    <td className="px-4 py-3 text-right">
                      {typeof r.imbalance === "number" ? (
                        <span className={
                          r.imbalance > 0.6 ? 'text-emerald-400' :
                          r.imbalance < 0.4 ? 'text-rose-400' :
                          'text-zinc-300'
                        }>
                          {r.imbalance.toFixed(2)}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {r.ws_lag_ms ? (
                        <span className={
                          r.ws_lag_ms < 50 ? 'text-emerald-400' :
                          r.ws_lag_ms < 100 ? 'text-amber-400' :
                          'text-rose-400'
                        }>
                          {r.ws_lag_ms}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>

                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <a
                          className="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 transition-colors text-xs"
                          href={toVenueLink(exchange, r.symbol)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {exchange === "mexc" ? "MEXC" : "Gate"}
                        </a>
                      </div>
                    </td>
                  </tr>
                );
              })}
              
              {sortedData.length === 0 && (
                <tr>
                  <td 
                    className="px-4 py-12 text-center text-zinc-500" 
                    colSpan={9 + depthLevels.length}
                  >
                    <div className="space-y-2">
                      <div className="text-lg">Ничего не найдено</div>
                      <div className="text-sm text-zinc-600">
                        Попробуйте ослабить фильтры или нажмите Refresh
                      </div>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          </div>
        </div>

        {/* Summary footer */}
        {sortedData.length > 0 && (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/20 p-4">
            <div className="flex flex-wrap gap-6 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-zinc-500">Total symbols:</span>
                <span className="font-medium text-zinc-200">{sortedData.length}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-zinc-500">Avg spread:</span>
                <span className="font-medium text-zinc-200">
                  {formatNumber(
                    sortedData.reduce((sum, r) => sum + r.spread_bps_ui, 0) / sortedData.length,
                    2
                  )} bps
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-zinc-500">Total daily volume:</span>
                <span className="font-medium text-zinc-200">
                  ${formatNumber(
                    sortedData.reduce((sum, r) => sum + r.daily_notional_usd, 0),
                    0
                  )}
                </span>
              </div>
              {depthLevels[0] && (
                <div className="flex items-center gap-2">
                  <span className="text-zinc-500">Avg depth@{depthLevels[0]}bps:</span>
                  <span className="font-medium text-zinc-200">
                    ${formatNumber(
                      sortedData.reduce((sum, r) => {
                        const depth = r.depth_at_bps?.[depthLevels[0]];
                        return sum + (depth ? Math.min(depth.bid_usd ?? 0, depth.ask_usd ?? 0) : 0);
                      }, 0) / sortedData.length,
                      0
                    )}
                  </span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}