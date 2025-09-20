// src/api/api.ts
import http from "@/lib/http";
import type { StrategyMetricsJSON, Position, UISnapshot } from "@/types/api";

/* ───────── локальные типы ответов exec ───────── */
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

export type ExecCancelResponse = {
  ok?: boolean;
};

/* ───────── helpers ───────── */
function idem(): string {
  // лёгкий idempotency-ключ без зависимостей
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

/* ───────── strategy control ───────── */
export async function apiStartSymbols(symbols: string[]) {
  const syms = dedupeSymbols(symbols);
  await http.post(
    "/api/strategy/start",
    { symbols: syms },
    { headers: { "X-Idempotency-Key": idem() } }
  );
}

export async function apiStopSymbols(symbols: string[], flatten = false) {
  const syms = dedupeSymbols(symbols);
  await http.post(
    "/api/strategy/stop",
    { symbols: syms, flatten },
    { headers: { "X-Idempotency-Key": idem() } }
  );
}

export async function apiStopAll(flatten = false) {
  await http.post(
    "/api/strategy/stop-all",
    { flatten },
    { headers: { "X-Idempotency-Key": idem() } }
  );
}

/* ───────── positions / metrics ───────── */
export async function apiGetPositions(symbols: string[]): Promise<Position[]> {
  const syms = dedupeSymbols(symbols);
  const params = syms.length
    ? syms.reduce((p, s) => {
        p.append("symbols", s);
        return p;
      }, new URLSearchParams())
    : undefined;

  const res = await http.get<Position[]>("/api/strategy/positions", { params });
  return res.data;
}

export async function apiGetAllPositions(): Promise<Position[]> {
  const res = await http.get<Position[]>("/api/strategy/positions");
  return res.data;
}

/** Альтернатива: напрямую из exec */
export async function apiGetExecPositions(symbols?: string[]): Promise<Position[]> {
  const syms = symbols ? dedupeSymbols(symbols) : [];
  const params =
    syms.length > 0
      ? syms.reduce((p, s) => {
          p.append("symbols", s);
          return p;
        }, new URLSearchParams())
      : undefined;

  const res = await http.get<Position[]>("/api/exec/positions", { params });
  return res.data;
}

export async function apiGetPosition(symbol: string): Promise<Position> {
  const res = await http.get<Position>("/api/strategy/position", {
    params: { symbol: symbol.trim().toUpperCase() },
  });
  return res.data;
}

export async function apiGetMetrics(): Promise<StrategyMetricsJSON> {
  const res = await http.get<StrategyMetricsJSON>("/api/strategy/metrics");
  return res.data;
}

/* ───────── UI snapshot (orders/fills/positions) ───────── */
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
  const res = await http.post<ExecPlaceResponse>("/api/exec/place", body, {
    headers: { "X-Idempotency-Key": idem() },
  });
  return res.data;
}

export async function apiFlatten(symbol: string): Promise<ExecFlattenResponse> {
  try {
    const res = await http.post<ExecFlattenResponse>(
      `/api/exec/flatten/${encodeURIComponent(symbol.trim().toUpperCase())}`,
      {},
      { headers: { "X-Idempotency-Key": idem() } }
    );
    return res.data;
  } catch {
    // graceful fallback на стратегию
    await apiStopSymbols([symbol], true);
    return { ok: true, flattened: symbol, position: await apiGetPosition(symbol) };
  }
}

export async function apiCancel(symbol: string): Promise<ExecCancelResponse> {
  const res = await http.post<ExecCancelResponse>(
    `/api/exec/cancel/${encodeURIComponent(symbol.trim().toUpperCase())}`,
    {},
    { headers: { "X-Idempotency-Key": idem() } }
  );
  return res.data;
}
