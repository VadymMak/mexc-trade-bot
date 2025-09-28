// src/api/api.ts
import http from "@/lib/http";
import type { StrategyMetricsJSON, Position, UISnapshot } from "@/types/api";

/* ───────── provider switch (NEW) ───────── */
export type Provider = "gate" | "mexc" | "binance";
export type Mode = "PAPER" | "DEMO" | "LIVE";
export type WatchlistItem = { symbol: string; running?: boolean };
export type WatchlistBulkOut = { items: WatchlistItem[]; revision?: number };

export type ProviderState = {
  active: Provider;
  mode: Mode;
  available: Provider[];
  ws_enabled: boolean;
  revision: number;
};

export async function getProviderConfig(): Promise<ProviderState> {
  const res = await http.get<ProviderState>("/api/config/provider");
  return res.data;
}

export async function switchProviderConfig(
  provider: Provider,
  mode: Mode,
  opts?: { idempotencyKey?: string }
): Promise<ProviderState> {
  // normalize defensively
  const prov = String(provider || "").toLowerCase() as Provider;
  const m = String(mode || "").toUpperCase() as Mode;
  if (!prov || !m) {
    throw new Error("provider and mode are required");
  }
  const headers: Record<string, string> = {};
  if (opts?.idempotencyKey) headers["X-Idempotency-Key"] = opts.idempotencyKey;

  const res = await http.post<ProviderState>(
    "/api/config/provider",
    { provider: prov, mode: m },
    { headers }
  );
  return res.data;
}

export async function getWatchlist(): Promise<WatchlistBulkOut> {
  const res = await http.get<unknown>("/api/ui/watchlist:bulk");
  const data = res.data as Record<string, unknown> | unknown[];

  // Accept { items: [...] } or plain array
  const items = Array.isArray(data)
    ? (data as WatchlistItem[])
    : Array.isArray((data as Record<string, unknown>)?.items)
    ? (data as Record<string, unknown>).items as WatchlistItem[]
    : [];

  const revision =
    !Array.isArray(data) && typeof (data as Record<string, unknown>)?.revision === "number"
      ? ((data as Record<string, unknown>).revision as number)
      : undefined;

  return { items, revision };
}

export async function setWatchlist(symbols: string[]): Promise<WatchlistBulkOut> {
  const body: WatchlistBulkIn = {
    symbols: symbols.map((s) => s.trim().toUpperCase()).filter(Boolean),
  };
  const res = await http.post<unknown>("/api/ui/watchlist:bulk", body, {
    headers: { "X-Idempotency-Key": idem() },
  });

  const data = res.data as Record<string, unknown> | unknown[];
  const items = Array.isArray(data)
    ? (data as WatchlistItem[])
    : Array.isArray((data as Record<string, unknown>)?.items)
    ? (data as Record<string, unknown>).items as WatchlistItem[]
    : [];

  const revision =
    !Array.isArray(data) && typeof (data as Record<string, unknown>)?.revision === "number"
      ? ((data as Record<string, unknown>).revision as number)
      : undefined;

  return { items, revision };
}

/* ───────── exec response types ───────── */
export type ExecPlaceResponse = {
  ok: boolean;
  client_order_id?: string;
  position?: Position;
};

export type ExecFlattenResponse = {
  ok: boolean;
  flattened?: string;
  position?: Position;
};

export type ExecCancelResponse = { ok?: boolean };

/* ───────── strategy control response types ───────── */
export type StrategyStartResponse = {
  ok?: boolean;
  started?: string[];
  running?: string[];
  message?: string;
};

export type StrategyStopResponse = {
  ok?: boolean;
  stopped?: string[];
  flattened?: string[];
  running?: string[];
  message?: string;
};

export type StopAllResponse = {
  ok?: boolean;
  stopped?: string[];
  flattened?: string[];
  message?: string;
};

/* ───────── strategy params (for /api/strategy/params) ───────── */
export type StrategyParams = {
  // Entry filters
  min_spread_bps: number;
  edge_floor_bps: number;
  imbalance_min: number;
  imbalance_max: number;
  enable_depth_check: boolean;
  absorption_x_bps: number;

  // Sizing & timing
  order_size_usd: number;
  timeout_exit_sec: number;
  max_concurrent_symbols: number;

  // Trade management
  take_profit_bps: number;
  stop_loss_bps: number;
  min_hold_ms: number;
  reenter_cooldown_ms: number;

  // Debug / test
  debug_force_entry: boolean;
};

/* ───────── helpers ───────── */
function idem(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function dedupeSymbols(syms: string[]): string[] {
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

/** Always include an idempotency key */
function postWithIdem<T>(url: string, body: unknown) {
  return http.post<T>(url, body, { headers: { "X-Idempotency-Key": idem() } });
}

/** Remove undefined fields from patch to avoid wiping server defaults */
function cleanPatch<T extends Record<string, unknown>>(patch: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(patch)) {
    if (v !== undefined) out[k] = v;
  }
  return out as T;
}

/* ───────── Position normalizer (no explicit any) ───────── */

type AnyRec = Record<string, unknown>;

function pick(obj: AnyRec, ...keys: string[]): unknown {
  for (const k of keys) {
    if (k in obj && obj[k] !== undefined) return obj[k];
  }
  return undefined;
}

/**
 * Normalizes various backend shapes to the UI Position type:
 * - symbol: string (UPPERCASE)
 * - qty: number
 * - avg: number (avg price / entry)
 * - uPnL: number (unrealized PnL)
 * - rPnL: number (realized PnL)
 */
function normalizePosition(raw: unknown): Position {
  const r = (raw ?? {}) as AnyRec;

  const symbol = String(pick(r, "symbol", "ticker", "SYMBOL") ?? "")
    .trim()
    .toUpperCase();

  const qty = Number(r["qty"] ?? r["quantity"] ?? 0);
  const avg = Number(r["avg"] ?? r["avg_price"] ?? 0);
  const upnl = Number(r["uPnL"] ?? r["unrealized_pnl"] ?? 0);
  const rpnl = Number(r["rPnL"] ?? r["realized_pnl"] ?? 0);

  return { symbol, qty, avg, upnl, rpnl };
}

/* ───────── strategy positions (legacy endpoint; kept for compatibility) ───────── */
export async function apiGetPositions(symbols: string[]): Promise<Position[]> {
  const syms = Array.from(
    new Set(
      symbols
        .map((s) => s?.trim().toUpperCase())
        .filter((s): s is string => Boolean(s))
    )
  );

  const params =
    syms.length > 0
      ? syms.reduce((p, s) => {
          p.append("symbols", s);
          return p;
        }, new URLSearchParams())
      : undefined;

  const res = await http.get<unknown[]>("/api/strategy/positions", { params });
  const arr = Array.isArray(res.data) ? res.data : [];
  return arr.map(normalizePosition);
}

/* ───────── UI session (for boot flow) ───────── */
export async function apiOpenSession(reset = false): Promise<Record<string, unknown>> {
  // POST /api/ui/session/open?reset=true|false
  const params = new URLSearchParams();
  if (reset) params.set("reset", "true");
  const url = params.toString()
    ? `/api/ui/session/open?${params.toString()}`
    : "/api/ui/session/open";
  const res = await http.post<Record<string, unknown>>(url, {});
  return res.data ?? {};
}

/* ───────── UI: watchlist bulk ───────── */
export type WatchlistBulkIn = { symbols: string[] };

/* ───────── strategy control ───────── */
export async function apiStartSymbols(symbols: string[]): Promise<StrategyStartResponse> {
  const syms = dedupeSymbols(symbols);
  const res = await postWithIdem<StrategyStartResponse>("/api/strategy/start", { symbols: syms });
  return res.data ?? {};
}

export async function apiStopSymbols(
  symbols: string[],
  flatten = false
): Promise<StrategyStopResponse> {
  const syms = dedupeSymbols(symbols);
  const res = await postWithIdem<StrategyStopResponse>("/api/strategy/stop", {
    symbols: syms,
    flatten,
  });
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

/** Partial update; backend returns the full, updated params */
export async function setStrategyParams(
  patch: Partial<StrategyParams>
): Promise<StrategyParams> {
  const res = await http.put<StrategyParams>("/api/strategy/params", cleanPatch(patch));
  return res.data;
}

/* ───────── UI: watchlist ───────── */
export async function apiWatchlistBulk(symbols: string[]) {
  const body: WatchlistBulkIn = {
    symbols: symbols.map((s) => s.trim().toUpperCase()).filter(Boolean),
  };
  const res = await http.post("/api/ui/watchlist:bulk", body, {
    headers: { "X-Idempotency-Key": idem() },
  });
  return res.data;
}

/* ───────── positions / metrics ───────── */

// Strategy positions (legacy, keep for compatibility)
export async function apiGetStrategyPositions(symbols: string[]): Promise<Position[]> {
  const syms = dedupeSymbols(symbols);
  const params = syms.length
    ? syms.reduce((p, s) => {
        p.append("symbols", s);
        return p;
      }, new URLSearchParams())
    : undefined;
  const res = await http.get<unknown[]>("/api/strategy/positions", { params });
  const arr = Array.isArray(res.data) ? res.data : [];
  return arr.map(normalizePosition);
}

export async function apiGetAllStrategyPositions(): Promise<Position[]> {
  const res = await http.get<unknown[]>("/api/strategy/positions");
  const arr = Array.isArray(res.data) ? res.data : [];
  return arr.map(normalizePosition);
}

// EXEC positions (truth, preferred)
export async function apiGetExecPositions(symbols?: string[]): Promise<Position[]> {
  const syms = symbols ? dedupeSymbols(symbols) : [];
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

export async function apiGetExecPosition(symbol: string): Promise<Position> {
  const sym = symbol.trim().toUpperCase();
  const res = await http.get<unknown>(`/api/exec/position/${encodeURIComponent(sym)}`);
  return normalizePosition(res.data);
}

// Default aliases → point to EXEC
export async function apiGetAllPositions(): Promise<Position[]> {
  return apiGetExecPositions();
}

export async function apiGetPosition(symbol: string): Promise<Position> {
  return apiGetExecPosition(symbol);
}

export async function apiGetMetrics(): Promise<StrategyMetricsJSON> {
  const res = await http.get<StrategyMetricsJSON>("/api/strategy/metrics");
  return res.data;
}

/* ───────── UI snapshot (positions / orders / fills) ───────── */
export async function apiGetUISnapshot(
  include?: Array<"positions" | "orders" | "fills">
): Promise<UISnapshot> {
  const params =
    include && include.length
      ? new URLSearchParams([["include", include.join(",")]])
      : undefined;
  const res = await http.get<UISnapshot>("/api/ui/snapshot", { params });
  return res.data;
}

/* ───────── execution (paper/live) ───────── */
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
  try {
    const res = await postWithIdem<ExecFlattenResponse>(
      `/api/exec/flatten/${encodeURIComponent(symbol.trim().toUpperCase())}`,
      {}
    );
    return res.data;
  } catch {
    // graceful fallback на стратегию
    await apiStopSymbols([symbol], true);
    return { ok: true, flattened: symbol, position: await apiGetPosition(symbol) };
  }
}

export async function apiCancel(symbol: string): Promise<ExecCancelResponse> {
  const res = await postWithIdem<ExecCancelResponse>(
    `/api/exec/cancel/${encodeURIComponent(symbol.trim().toUpperCase())}`,
    {}
  );
  return res.data;
}
