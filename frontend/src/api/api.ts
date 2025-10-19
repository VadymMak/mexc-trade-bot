// src/api/api.ts
import http from "@/lib/http";
import type { StrategyMetricsJSON, UISnapshot } from "@/types/api";
import type { Position } from "@/types/index";
import type {
  Provider,
  Mode,
  ProviderState,
  WatchlistItem,
  WatchlistBulkIn,
  WatchlistBulkOut,
  StrategyParams,
  ExecPlaceResponse,
  ExecFlattenResponse,
  ExecCancelResponse,
  StrategyStartResponse,
  StrategyStopResponse,
  StopAllResponse,
  ScannerRow,
  GetScannerOpts,
  ScannerTopTieredResponse,
} from "@/types";

/* ───────── helpers ───────── */
function idem(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
function dedupeSymbols(syms?: string[]): string[] {
  if (!syms) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const s of syms) {
    const sym = s?.trim().toUpperCase();
    if (sym && !seen.has(sym)) {
      seen.add(sym);
      out.push(sym);
    }
  }
  return out;
}
function postWithIdem<T>(url: string, body: unknown) {
  return http.post<T>(url, body, { headers: { "X-Idempotency-Key": idem() } });
}
function cleanPatch<T extends Record<string, unknown>>(patch: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(patch)) if (v !== undefined) out[k] = v;
  return out as T;
}
async function safeFetch<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

/* ───────── provider config ───────── */
export async function getProviderConfig(): Promise<ProviderState> {
  const res = await http.get<ProviderState>("/api/config/provider");
  return res.data;
}
export async function switchProviderConfig(
  provider: Provider,
  mode: Mode,
  opts?: { idempotencyKey?: string }
): Promise<ProviderState> {
  const prov = provider.toLowerCase() as Provider;
  const m = mode.toUpperCase() as Mode;
  const headers: Record<string, string> = {};
  if (opts?.idempotencyKey) headers["X-Idempotency-Key"] = opts.idempotencyKey;
  const res = await http.post<ProviderState>(
    "/api/config/provider",
    { provider: prov, mode: m },
    { headers }
  );
  return res.data;
}

/* ───────── watchlist ───────── */
export async function getWatchlist(): Promise<WatchlistBulkOut> {
  const normalizeItems = (arr: unknown[]): WatchlistItem[] => {
    const out: WatchlistItem[] = [];
    for (const it of arr) {
      if (typeof it === "string") {
        const sym = it.trim().toUpperCase();
        if (sym) out.push({ symbol: sym });
      } else if (typeof it === "object" && it !== null) {
        const rec = it as Record<string, unknown>;
        const symVal = rec.symbol;
        const sym = typeof symVal === "string" ? symVal.trim().toUpperCase() : "";
        if (!sym) continue;
        const runVal = rec.running;
        out.push({ symbol: sym, running: typeof runVal === "boolean" ? runVal : undefined });
      }
    }
    return out;
  };

  const parse = (data: unknown): WatchlistBulkOut => {
    if (typeof data === "object" && data !== null) {
      const root = data as Record<string, unknown>;

      const ui =
        typeof root.ui_state === "object" && root.ui_state !== null
          ? (root.ui_state as Record<string, unknown>)
          : undefined;
      const wl =
        ui && typeof ui.watchlist === "object" && ui.watchlist !== null
          ? (ui.watchlist as Record<string, unknown>)
          : undefined;

      if (wl) {
        if (Array.isArray(wl.items)) {
          return {
            items: normalizeItems(wl.items as unknown[]),
            revision: typeof wl.revision === "number" ? (wl.revision as number) : undefined,
          };
        }
        if (Array.isArray(wl.symbols)) {
          return {
            items: normalizeItems(wl.symbols as unknown[]),
            revision: typeof wl.revision === "number" ? (wl.revision as number) : undefined,
          };
        }
      }

      if (Array.isArray(root.items)) {
        return {
          items: normalizeItems(root.items as unknown[]),
          revision: typeof root.revision === "number" ? (root.revision as number) : undefined,
        };
      }

      if (Array.isArray(root.symbols)) {
        return {
          items: normalizeItems(root.symbols as unknown[]),
          revision: typeof root.revision === "number" ? (root.revision as number) : undefined,
        };
      }
    }

    if (Array.isArray(data)) return { items: normalizeItems(data as unknown[]) };

    return { items: [] };
  };

  const statusOf = (err: unknown): number | undefined => {
    if (typeof err !== "object" || err === null) return undefined;
    const resp = (err as Record<string, unknown>).response;
    if (typeof resp !== "object" || resp === null) return undefined;
    const st = (resp as Record<string, unknown>).status;
    return typeof st === "number" ? st : undefined;
  };

  try {
    const resPost = await http.post<unknown>(
      "/api/ui/watchlist:bulk",
      { symbols: [] as string[] },
      { headers: { "X-Idempotency-Key": idem() } }
    );
    return parse(resPost.data);
  } catch (e) {
    const st = statusOf(e);
    if (st === 404 || st === 405 || st === 400 || st === undefined) {
      try {
        const resState = await http.get<unknown>("/api/ui/state");
        return parse(resState.data);
      } catch {
        try {
          const resGet = await http.get<unknown>("/api/ui/watchlist:bulk");
          return parse(resGet.data);
        } catch {
          return { items: [] };
        }
      }
    }
    throw e;
  }
}

export async function setWatchlist(symbols: string[]): Promise<WatchlistBulkOut> {
  const body: WatchlistBulkIn = { symbols: dedupeSymbols(symbols) };
  const res = await http.post<unknown>("/api/ui/watchlist:bulk", body, {
    headers: { "X-Idempotency-Key": idem() },
  });

  const data = res.data as Record<string, unknown> | unknown[];
  const items = Array.isArray(data)
    ? (data as WatchlistItem[])
    : Array.isArray((data as Record<string, unknown>)?.items)
    ? ((data as Record<string, unknown>).items as WatchlistItem[])
    : [];

  const revision =
    !Array.isArray(data) && typeof (data as Record<string, unknown>)?.revision === "number"
      ? ((data as Record<string, unknown>).revision as number)
      : undefined;

  return { items, revision };
}

/** Совместимость: если передали символы → пишет; если нет → просто читает. */
export async function apiWatchlistBulk(symbols?: string[]): Promise<WatchlistBulkOut> {
  const syms = dedupeSymbols(symbols);
  return syms.length > 0 ? setWatchlist(syms) : getWatchlist();
}

/* ───────── position normalization ───────── */
type AnyRec = Record<string, unknown>;
function pick(obj: AnyRec, ...keys: string[]): unknown {
  for (const k of keys) if (k in obj && obj[k] !== undefined) return obj[k];
  return undefined;
}
function normalizePosition(raw: unknown): Position {
  const r = (raw ?? {}) as AnyRec;
  const symbol = String(pick(r, "symbol", "ticker", "SYMBOL") ?? "").trim().toUpperCase();
  const qty = Number(pick(r, "qty", "quantity") ?? 0);
  const avg_price = Number(pick(r, "avg_price", "avg") ?? 0);
  const realized_pnl = Number(pick(r, "realized_pnl", "rpnl", "realized_usd") ?? 0);
  return { symbol, qty, avg_price, realized_pnl };
}

/* ───────── strategy control ───────── */
export async function apiStartSymbols(symbols: string[]): Promise<StrategyStartResponse> {
  const syms = dedupeSymbols(symbols);
  const res = await postWithIdem<StrategyStartResponse>("/api/strategy/start", { symbols: syms });
  return res.data ?? {};
}
export async function apiStopSymbols(symbols: string[], flatten = false): Promise<StrategyStopResponse> {
  const syms = dedupeSymbols(symbols);
  const res = await postWithIdem<StrategyStopResponse>("/api/strategy/stop", { symbols: syms, flatten });
  return res.data ?? {};
}
export async function apiStopAll(flatten = false): Promise<StopAllResponse> {
  const res = await postWithIdem<StopAllResponse>("/api/strategy/stop-all", { flatten });
  return res.data ?? {};
}

/* ───────── strategy params ───────── */
export async function getStrategyParams(): Promise<StrategyParams> {
  const res = await http.get<StrategyParams>("/api/strategy/params");
  return res.data;
}
export async function setStrategyParams(patch: Partial<StrategyParams>): Promise<StrategyParams> {
  const res = await http.put<StrategyParams>("/api/strategy/params", cleanPatch(patch));
  return res.data;
}

/* ───────── positions (canonical) ───────── */
export async function apiGetExecPositions(symbols?: string[]): Promise<Position[]> {
  const syms = dedupeSymbols(symbols);
  const params =
    syms.length > 0
      ? syms.reduce((p, s) => {
          p.append("symbols", s);
          return p;
        }, new URLSearchParams())
      : undefined;

  const res = await http.get<unknown[]>("/api/exec/positions", { params });
  const arr = Array.isArray(res.data) ? res.data : [];
  return arr.map(normalizePosition);
}
export async function apiEnsurePositions(symbols?: string[]): Promise<Position[]> {
  const data = await safeFetch(() => apiGetExecPositions(symbols));
  return Array.isArray(data) ? data : [];
}
export async function apiGetExecPosition(symbol: string): Promise<Position> {
  const sym = symbol.trim().toUpperCase();
  const res = await http.get<unknown>(`/api/exec/position/${encodeURIComponent(sym)}`);
  return normalizePosition(res.data);
}

/* ───────── execution ───────── */
export async function apiPlaceOrder(args: {
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  price?: number;
  tag?: string;
}): Promise<ExecPlaceResponse> {
  const body = {
    symbol: args.symbol.trim().toUpperCase(),
    side: args.side,
    qty: args.qty,
    price: args.price ?? 0,
    tag: args.tag ?? "manual",
  };
  const res = await postWithIdem<ExecPlaceResponse>("/api/exec/place", body);
  return res.data;
}
export async function apiFlatten(symbol: string): Promise<ExecFlattenResponse> {
  const sym = symbol.trim().toUpperCase();
  try {
    const res = await postWithIdem<ExecFlattenResponse>(
      `/api/exec/flatten/${encodeURIComponent(sym)}`,
      {}
    );
    return res.data;
  } catch {
    await apiStopSymbols([sym], true);
    return { ok: true, flattened: sym, position: await apiGetExecPosition(sym) };
  }
}
export async function apiCancel(symbol: string): Promise<ExecCancelResponse> {
  const sym = symbol.trim().toUpperCase();
  const res = await postWithIdem<ExecCancelResponse>(
    `/api/exec/cancel/${encodeURIComponent(sym)}`,
    {}
  );
  return res.data;
}

/* ───────── metrics & snapshot ───────── */
export async function apiGetMetrics(): Promise<StrategyMetricsJSON> {
  const res = await http.get<StrategyMetricsJSON>("/api/strategy/metrics");
  return res.data;
}
export async function apiGetUISnapshot(
  include?: Array<"positions" | "orders" | "fills">
): Promise<UISnapshot> {
  const params = include?.length ? new URLSearchParams([["include", include.join(",")]]) : undefined;
  const res = await http.get<UISnapshot>("/api/ui/snapshot", { params });
  return res.data;
}

/* ───────── scanner ───────── */

/** Accept both new snake_case and legacy camelCase fields in a typed way. */
type GetScannerOptsCompat = GetScannerOpts & {
  // legacy camelCase accepted by older UI code
  includeStables?: boolean;
  excludeLeveraged?: boolean;
  minUsd?: number;
  minDepth5Usd?: number;
  minDepth10Usd?: number;
  minTradesPerMin?: number;
};

function normalizeScannerRows(rows: ScannerRow[]): ScannerRow[] {
  return rows.map((r) => {
    const bid = Number(r.bid || 0);
    const ask = Number(r.ask || 0);
    const spreadAbs = r.spread_abs ?? (bid > 0 && ask > 0 ? ask - bid : undefined);
    const spreadPct =
      r.spread_pct ??
      (typeof spreadAbs === "number" && bid > 0 && ask > 0
        ? (spreadAbs / ((ask + bid) * 0.5)) * 100
        : undefined);
    const spreadBps = r.spread_bps ?? (typeof spreadPct === "number" ? spreadPct * 100 : undefined);
    return {
      ...r,
      symbol: String(r.symbol || "").toUpperCase(),
      bid,
      ask,
      spread_abs: spreadAbs,
      spread_pct: spreadPct,
      spread_bps: spreadBps,
    };
  });
}

function buildScannerParams(opts: GetScannerOptsCompat): URLSearchParams {
  const params = new URLSearchParams();

  // server-native params
  const quote = (opts.quote ?? "USDT").toUpperCase();
  const limit = Number.isFinite(opts.limit as number) ? (opts.limit as number) : 30;
  params.set("quote", quote);
  params.set("limit", String(limit));

  if (opts.preset) params.set("preset", String(opts.preset));
  if (typeof opts.fetch_candles === "boolean") params.set("fetch_candles", String(opts.fetch_candles));
  if (Array.isArray(opts.depth_bps_levels) && opts.depth_bps_levels.length > 0) {
    params.set("depth_bps_levels", opts.depth_bps_levels.join(","));
  }
  if (typeof opts.rotation === "boolean") params.set("rotation", String(opts.rotation));
  if (typeof opts.explain === "boolean") params.set("explain", String(opts.explain));

  // spread (UI in bps → server in percent)
  if (typeof (opts as { max_spread_bps?: number }).max_spread_bps === "number") {
    params.set("min_spread_pct", String((opts as { max_spread_bps: number }).max_spread_bps / 100));
  } else if (typeof opts.minBps === "number") {
    params.set("min_spread_pct", String(opts.minBps / 100));
  }

  // volumes / activity
  if (typeof opts.min_quote_vol_usd === "number") {
    params.set("min_quote_vol_usd", String(opts.min_quote_vol_usd));
  } else if (typeof opts.minUsd === "number") {
    params.set("min_quote_vol_usd", String(opts.minUsd));
  }
  if (typeof opts.min_usd_per_min === "number") params.set("min_usd_per_min", String(opts.min_usd_per_min));
  if (typeof (opts as { min_median_trade_usd?: number }).min_median_trade_usd === "number")
    params.set("min_median_trade_usd", String((opts as { min_median_trade_usd: number }).min_median_trade_usd));
  if (typeof (opts as { min_vol_pattern?: number }).min_vol_pattern === "number")
    params.set("min_vol_pattern", String((opts as { min_vol_pattern: number }).min_vol_pattern));
  if (typeof (opts as { max_atr_proxy?: number }).max_atr_proxy === "number")
    params.set("max_atr_proxy", String((opts as { max_atr_proxy: number }).max_atr_proxy));
  if (typeof (opts as { activity_ratio?: number }).activity_ratio === "number")
    params.set("activity_ratio", String((opts as { activity_ratio: number }).activity_ratio));

  // depth / trades
  if (typeof opts.min_depth5_usd === "number") {
    params.set("min_depth5_usd", String(opts.min_depth5_usd));
  } else if (typeof opts.minDepth5Usd === "number") {
    params.set("min_depth5_usd", String(opts.minDepth5Usd));
  }
  if (typeof opts.min_depth10_usd === "number") {
    params.set("min_depth10_usd", String(opts.min_depth10_usd));
  } else if (typeof opts.minDepth10Usd === "number") {
    params.set("min_depth10_usd", String(opts.minDepth10Usd));
  }
  if (typeof opts.min_trades_per_min === "number") {
    params.set("min_trades_per_min", String(opts.min_trades_per_min));
  } else if (typeof opts.minTradesPerMin === "number") {
    params.set("min_trades_per_min", String(opts.minTradesPerMin));
  }

  // booleans
  if (typeof opts.include_stables === "boolean") {
    params.set("include_stables", String(opts.include_stables));
  } else if (typeof opts.includeStables === "boolean") {
    params.set("include_stables", String(opts.includeStables));
  }

  if (typeof opts.exclude_leveraged === "boolean") {
    params.set("exclude_leveraged", String(opts.exclude_leveraged));
  } else if (typeof opts.excludeLeveraged === "boolean") {
    params.set("exclude_leveraged", String(opts.excludeLeveraged));
  }

  // whitelist
  if (Array.isArray((opts as { symbols?: string[] }).symbols)) {
    const list = dedupeSymbols((opts as { symbols: string[] }).symbols);
    if (list.length) params.set("symbols", list.join(","));
  } else if (typeof (opts as { symbols?: string }).symbols === "string" && (opts as { symbols: string }).symbols.trim()) {
    params.set("symbols", (opts as { symbols: string }).symbols.trim());
  }

  return params;
}

export async function getScannerGateTop(opts: GetScannerOpts = {}): Promise<ScannerRow[]> {
  const res = await http.get<ScannerRow[]>("/api/scanner/gate/top", { params: buildScannerParams(opts) });
  const arr = Array.isArray(res.data) ? res.data : [];
  return normalizeScannerRows(arr);
}

export async function getScannerMexcTop(opts: GetScannerOpts = {}): Promise<ScannerRow[]> {
  const res = await http.get<ScannerRow[]>("/api/scanner/mexc/top", { params: buildScannerParams(opts) });
  const arr = Array.isArray(res.data) ? res.data : [];
  return normalizeScannerRows(arr);
}

export async function getScannerTopAny(
  exchange: "gate" | "mexc" | "all",
  opts: GetScannerOpts = {}
): Promise<ScannerRow[]> {
  const params = buildScannerParams(opts);
  params.set("exchange", exchange);
  const res = await http.get<ScannerRow[]>("/api/scanner/top", { params });
  const arr = Array.isArray(res.data) ? res.data : [];
  return normalizeScannerRows(arr);
}

export async function getScannerGateTopTiered(opts: GetScannerOpts = {}): Promise<ScannerTopTieredResponse> {
  const params = buildScannerParams({
    preset: opts.preset ?? "balanced",
    quote: (opts.quote ?? "USDT") as string,
    limit: Number.isFinite(opts.limit as number) ? (opts.limit as number) : 50,
    fetch_candles: typeof opts.fetch_candles === "boolean" ? opts.fetch_candles : true,
    depth_bps_levels: Array.isArray(opts.depth_bps_levels) ? opts.depth_bps_levels : [5, 10],
    explain: typeof opts.explain === "boolean" ? opts.explain : false,
    rotation: typeof opts.rotation === "boolean" ? opts.rotation : false,
  });
  const res = await http.get<ScannerTopTieredResponse>("/api/scanner/gate/top_tiered", { params });
  return res.data;
}

export async function getScannerMexcTopTiered(opts: GetScannerOpts = {}): Promise<ScannerTopTieredResponse> {
  const params = buildScannerParams({
    preset: opts.preset ?? "balanced",
    quote: (opts.quote ?? "USDT") as string,
    limit: Number.isFinite(opts.limit as number) ? (opts.limit as number) : 50,
    fetch_candles: typeof opts.fetch_candles === "boolean" ? opts.fetch_candles : true,
    depth_bps_levels: Array.isArray(opts.depth_bps_levels) ? opts.depth_bps_levels : [5, 10],
    explain: typeof opts.explain === "boolean" ? opts.explain : false,
    rotation: typeof opts.rotation === "boolean" ? opts.rotation : false,
  });
  const res = await http.get<ScannerTopTieredResponse>("/api/scanner/mexc/top_tiered", { params });
  return res.data;
}

/* ───────── legacy alias for compatibility ───────── */
export type TickerRow = ScannerRow;
export async function getTickers(params?: {
  min_quote_vol_usd?: number;
  min_spread_pct?: number;
  include_stables?: boolean;
  exclude_leveraged?: boolean;
  limit?: number;
}): Promise<TickerRow[]> {
  const rows = await getScannerGateTop({
    quote: "USDT",
    minBps: typeof params?.min_spread_pct === "number" ? params.min_spread_pct * 100 : undefined,
    min_quote_vol_usd: params?.min_quote_vol_usd,
    limit: params?.limit,
    include_stables: params?.include_stables,
    exclude_leveraged: params?.exclude_leveraged,
  });
  return rows;
}
export async function startStrategy(symbol: string) {
  return apiStartSymbols([symbol.trim().toUpperCase()]);
}

/* ───────── UI session (compat) ───────── */
export async function apiOpenSession(reset = false): Promise<Record<string, unknown>> {
  const params = new URLSearchParams();
  if (reset) params.set("reset", "true");
  const url = params.toString()
    ? `/api/ui/session/open?${params.toString()}`
    : "/api/ui/session/open";
  const res = await http.post<Record<string, unknown>>(url, {});
  return res.data ?? {};
}
