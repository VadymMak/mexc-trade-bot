// src/pages/LiquidityScanner.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiStartSymbols, getScannerTopAny } from "@/api/api";
import type { ScannerRow } from "@/types"; // ← types from central index
import { useInterval } from "@/hooks/useInterval";
import { formatNumber } from "@/utils/format";
import PageToolbar from "@/components/layout/PageToolbar";

type QuoteFilter = "ALL" | "USDT" | "USDC" | "FDUSD" | "BUSD";
type ExchangeFilter = "gate" | "mexc" | "all";

/** Row with UI-derivatives we compute locally */
type UiRow = ScannerRow & {
  mid: number;
  spread_bps_ui: number;      // guaranteed number for display
  daily_notional_usd: number; // proxy from quote_volume_24h
  quote_ccy: string;          // parsed quote
  base_ccy: string;           // parsed base
};

function quoteOf(symbol: string): string {
  const s = symbol.toUpperCase();
  for (const q of ["USDT", "USDC", "FDUSD", "BUSD"]) if (s.endsWith(q)) return q;
  return "OTHER";
}
function baseOf(symbol: string): string {
  const s = symbol.toUpperCase();
  for (const q of ["USDT", "USDC", "FDUSD", "BUSD"]) if (s.endsWith(q)) return s.slice(0, -q.length);
  return s;
}
function toVenueLink(exchange: ExchangeFilter, sym: string): string {
  const base = baseOf(sym);
  const quote = quoteOf(sym);
  if (exchange === "mexc") return `https://www.mexc.com/exchange/${base}_${quote}`;
  return `https://www.gate.io/trade/${base}_${quote}`;
}

export default function LiquidityScanner() {
  // refresh & run-state
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(true);
  const [intervalMs] = useState<number>(4000);

  // new exchange selector
  const [exchange, setExchange] = useState<ExchangeFilter>("gate");

  // core filters
  const [quote, setQuote] = useState<QuoteFilter>("USDT");
  const [minBps, setMinBps] = useState<number>(10);            // UI bps → server percent
  const [minUsdDay, setMinUsdDay] = useState<number>(10_000);  // “Min $/day”
  const [minDepth5, setMinDepth5] = useState<number>(10_000);
  const [minDepth10, setMinDepth10] = useState<number>(10_000);
  const [minTradesPerMin, setMinTradesPerMin] = useState<number>(50);
  const [limit, setLimit] = useState<number>(100);
  const [includeStables, setIncludeStables] = useState<boolean>(false);
  const [excludeLeveraged, setExcludeLeveraged] = useState<boolean>(true);

  // data
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [routeError, setRouteError] = useState<string | null>(null);

  // prevent stale results overwriting fresh ones
  const reqSeq = useRef(0);

  const fetchRows = useCallback(async () => {
    const myReq = ++reqSeq.current;
    setLoading(true);
    setRouteError(null);
    try {
      const data = await getScannerTopAny(exchange, {
        quote: quote === "ALL" ? "USDT" : quote, // keep backend-compatible; UI filters below
        explain: true,
        limit,
        minBps,
        minUsd: minUsdDay,
        minDepth5Usd: minDepth5,
        minDepth10Usd: minDepth10,
        minTradesPerMin,
        includeStables,
        excludeLeveraged,
      });

      // only commit if this is the latest request
      if (myReq === reqSeq.current) {
        setRows(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      if (myReq === reqSeq.current) {
        console.error("getScannerTopAny failed:", e);
        setRows([]);
        setRouteError("Request failed — check backend logs or relax thresholds and Refresh.");
      }
    } finally {
      if (myReq === reqSeq.current) setLoading(false);
    }
  }, [
    exchange,
    quote,
    minBps,
    minUsdDay,
    minDepth5,
    minDepth10,
    minTradesPerMin,
    includeStables,
    excludeLeveraged,
    limit,
  ]);

  useEffect(() => {
    void fetchRows();
  }, [fetchRows]);

  useInterval(
    () => {
      if (running) void fetchRows();
    },
    running ? Math.max(1000, intervalMs) : null
  );

  // map + client-side post-filtering + sorting
  const data: UiRow[] = useMemo(() => {
    const items = (rows ?? []).map<UiRow>((r) => {
      const mid = r.bid > 0 && r.ask > 0 ? (r.bid + r.ask) / 2 : Math.max(r.bid, r.ask, 0);
      const spread_bps_ui =
        typeof r.spread_bps === "number"
          ? r.spread_bps
          : typeof r.spread_pct === "number"
          ? r.spread_pct * 100
          : r.bid > 0 && r.ask > 0
          ? ((r.ask - r.bid) / ((r.ask + r.bid) / 2)) * 10_000
          : 0;
      const daily_notional_usd = Number(r.quote_volume_24h || 0);
      return {
        ...r,
        mid,
        spread_bps_ui,
        daily_notional_usd,
        quote_ccy: quoteOf(r.symbol),
        base_ccy: baseOf(r.symbol),
      };
    });

    // Hide stub error rows from table (we’ll show banner instead)
    const withoutStubs = items.filter((r) => r.symbol !== "__error__");

    // UI quote filter (when "ALL", keep what server returned)
    const quoteFiltered =
      quote === "ALL" ? withoutStubs : withoutStubs.filter((r) => r.quote_ccy === quote);

    // Extra client-side constraints (if backend didn’t filter some)
    const withTape = quoteFiltered.filter((r) => {
      const depthOK5 =
        r.depth5_bid_usd === undefined ||
        r.depth5_ask_usd === undefined ||
        (r.depth5_bid_usd >= minDepth5 && r.depth5_ask_usd >= minDepth5);
      const depthOK10 =
        r.depth10_bid_usd === undefined ||
        r.depth10_ask_usd === undefined ||
        (r.depth10_bid_usd >= minDepth10 && r.depth10_ask_usd >= minDepth10);
      const tradesOK =
        typeof r.trades_per_min !== "number" || r.trades_per_min >= minTradesPerMin;
      const dayOK = r.daily_notional_usd >= minUsdDay;
      return depthOK5 && depthOK10 && tradesOK && dayOK;
    });

    // sort best-first by spread bps desc
    return withTape.sort((a, b) => b.spread_bps_ui - a.spread_bps_ui).slice(0, limit);
  }, [rows, quote, minDepth5, minDepth10, minTradesPerMin, minUsdDay, limit]);

  const onStartAll = useCallback(async () => {
    try {
      const symbols = data.map((r) => r.symbol);
      if (symbols.length === 0) return;
      await apiStartSymbols(symbols);
      alert(`Sent ${symbols.length} symbol(s) to strategy`);
    } catch (e) {
      console.error(e);
      alert("Failed to start some symbols");
    }
  }, [data]);

  const copySymbols = useCallback(async () => {
    const list = data.map((r) => r.symbol).join("\n");
    try {
      await navigator.clipboard.writeText(list);
      alert("Symbols copied to clipboard");
    } catch {
      alert(list);
    }
  }, [data]);

  const openOnVenue = useCallback(() => {
    const ex: ExchangeFilter = exchange;
    data.slice(0, 10).forEach((r) => window.open(toVenueLink(ex, r.symbol), "_blank"));
  }, [data, exchange]);

  return (
    <div className="p-4 space-y-4">
      {/* header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Liquidity Scanner</h1>
          <p className="text-zinc-400 text-sm">Фильтры и отбор ликвидных пар для стратегии.</p>
        </div>

        <div className="flex items-center gap-2">
          <PageToolbar />
          <div className="mx-1 h-6 w-px bg-zinc-700/60" />
          <button
            className="px-3 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700"
            onClick={fetchRows}
            disabled={loading}
            type="button"
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
          <button
            className={`px-3 py-1 rounded-lg ${running ? "bg-emerald-700" : "bg-zinc-800"} hover:bg-zinc-700`}
            onClick={() => setRunning((v) => !v)}
            title={running ? "Stop auto-refresh" : "Start auto-refresh"}
            type="button"
          >
            {running ? "Stop" : "Start"}
          </button>
        </div>
      </div>

      {/* optional venue error banner */}
      {routeError && (
        <div className="rounded-lg border border-amber-600/40 bg-amber-900/20 px-3 py-2 text-amber-200">
          {routeError}
        </div>
      )}

      {/* filters */}
      <div className="grid grid-cols-1 xl:grid-cols-10 gap-3">
        <div className="flex flex-col">
          <label className="text-xs text-zinc-400 mb-1">Exchange</label>
          <select
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2"
            value={exchange}
            onChange={(e) => setExchange(e.target.value as ExchangeFilter)}
          >
            <option value="gate">gate</option>
            <option value="mexc">mexc</option>
            <option value="all">all</option>
          </select>
        </div>

        <div className="flex flex-col">
          <label className="text-xs text-zinc-400 mb-1">Quote</label>
          <select
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2"
            value={quote}
            onChange={(e) => setQuote(e.target.value as QuoteFilter)}
          >
            <option value="ALL">ALL</option>
            <option value="USDT">USDT</option>
            <option value="USDC">USDC</option>
            <option value="FDUSD">FDUSD</option>
            <option value="BUSD">BUSD</option>
          </select>
        </div>

        <div className="flex flex-col">
          <label className="text-xs text-zinc-400 mb-1">Min spread (bps)</label>
          <input
            type="number"
            step="1"
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2"
            value={minBps}
            onChange={(e) => setMinBps(Number(e.target.value))}
          />
        </div>

        <div className="flex flex-col">
          <label className="text-xs text-zinc-400 mb-1">Min $/day</label>
          <input
            type="number"
            step="100"
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2"
            value={minUsdDay}
            onChange={(e) => setMinUsdDay(Number(e.target.value))}
          />
        </div>

        <div className="flex flex-col">
          <label className="text-xs text-zinc-400 mb-1">Min depth @5bps (USD)</label>
          <input
            type="number"
            step="100"
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2"
            value={minDepth5}
            onChange={(e) => setMinDepth5(Number(e.target.value))}
          />
        </div>

        <div className="flex flex-col">
          <label className="text-xs text-zinc-400 mb-1">Min depth @10bps (USD)</label>
          <input
            type="number"
            step="100"
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2"
            value={minDepth10}
            onChange={(e) => setMinDepth10(Number(e.target.value))}
          />
        </div>

        <div className="flex flex-col">
          <label className="text-xs text-zinc-400 mb-1">Min trades/min</label>
          <input
            type="number"
            step="5"
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2"
            value={minTradesPerMin}
            onChange={(e) => setMinTradesPerMin(Number(e.target.value))}
          />
        </div>

        <div className="flex flex-col">
          <label className="text-xs text-zinc-400 mb-1">Limit</label>
          <input
            type="number"
            step="10"
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
          />
        </div>

        <div className="flex flex-col justify-end">
          <div className="flex items-center gap-4">
            <label className="inline-flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeStables}
                onChange={(e) => setIncludeStables(e.target.checked)}
              />
              <span>Include stables</span>
            </label>
            <label className="inline-flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={excludeLeveraged}
                onChange={(e) => setExcludeLeveraged(e.target.checked)}
              />
              <span>Exclude leveraged</span>
            </label>
          </div>
        </div>
      </div>

      {/* actions row */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm text-zinc-400">
          Found: <span className="text-zinc-2 00">{data.length}</span>
        </div>
        <div className="mx-2 h-6 w-px bg-zinc-700/60" />
        <button className="px-3 py-1 rounded-lg bg-emerald-700 hover:bg-emerald-600" onClick={onStartAll} type="button">
          Send all to Strategy
        </button>
        <button className="px-3 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700" onClick={copySymbols} type="button">
          Copy
        </button>
        <button className="px-3 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700" onClick={openOnVenue} type="button">
          Open on {exchange === "all" ? "Gate" : exchange}
        </button>
      </div>

      {/* table */}
      <div className="overflow-auto rounded-xl border border-zinc-800">
        <table className="min-w-full text-sm">
          <thead className="bg-zinc-900/60 sticky top-0 z-10">
            <tr className="text-left">
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2">Mid</th>
              <th className="px-3 py-2">Bid</th>
              <th className="px-3 py-2">Ask</th>
              <th className="px-3 py-2">Spread (bps)</th>
              <th className="px-3 py-2">Depth@5bps</th>
              <th className="px-3 py-2">Depth@10bps</th>
              <th className="px-3 py-2">Trades/min</th>
              <th className="px-3 py-2">$ / min</th>
              <th className="px-3 py-2">Median trade</th>
              <th className="px-3 py-2">Daily $</th>
              <th className="px-3 py-2">Imbalance</th>
              <th className="px-3 py-2">WS lag, ms</th>
              <th className="px-3 py-2 text-right">Links</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.symbol} className="border-t border-zinc-800 hover:bg-zinc-900/30">
                <td className="px-3 py-2 font-medium">{`${baseOf(r.symbol)}/${quoteOf(r.symbol)}`}</td>
                <td className="px-3 py-2">{formatNumber(r.mid, 6)}</td>
                <td className="px-3 py-2">{formatNumber(r.bid, 6)}</td>
                <td className="px-3 py-2">{formatNumber(r.ask, 6)}</td>
                <td className="px-3 py-2">{formatNumber(r.spread_bps_ui, 2)}</td>

                <td className="px-3 py-2">
                  <div>
                    {formatNumber(r.depth5_bid_usd ?? 0, 0)} <span className="text-zinc-400">bid</span>
                  </div>
                  <div className="text-rose-300">
                    {formatNumber(r.depth5_ask_usd ?? 0, 0)} <span className="text-zinc-400">ask</span>
                  </div>
                </td>

                <td className="px-3 py-2">
                  <div>
                    {formatNumber(r.depth10_bid_usd ?? 0, 0)} <span className="text-zinc-400">bid</span>
                  </div>
                  <div className="text-rose-300">
                    {formatNumber(r.depth10_ask_usd ?? 0, 0)} <span className="text-zinc-400">ask</span>
                  </div>
                </td>

                <td className="px-3 py-2">{r.trades_per_min ?? "—"}</td>
                <td className="px-3 py-2">{formatNumber(r.usd_per_min ?? 0, 0)}</td>
                <td className="px-3 py-2">{formatNumber(r.median_trade_usd ?? 0, 0)}</td>
                <td className="px-3 py-2">{formatNumber(r.daily_notional_usd, 0)}</td>
                <td className="px-3 py-2">{typeof r.imbalance === "number" ? r.imbalance.toFixed(2) : "—"}</td>
                <td className="px-3 py-2">{r.ws_lag_ms ?? "—"}</td>

                <td className="px-3 py-2">
                  <div className="flex justify-end gap-2">
                    <a
                      className="px-3 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700"
                      href={toVenueLink(exchange, r.symbol)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {exchange === "mexc" ? "MEXC" : "Gate"}
                    </a>
                  </div>
                </td>
              </tr>
            ))}
            {data.length === 0 && (
              <tr>
                <td className="px-3 py-6 text-center text-zinc-500" colSpan={14}>
                  Ничего не найдено под фильтры — ослабьте пороги или нажмите Refresh.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
