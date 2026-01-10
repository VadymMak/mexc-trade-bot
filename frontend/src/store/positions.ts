// src/store/positions.ts
import { create } from "zustand";
import { useProvider } from "@/store/provider";
import type { Position } from "@/types";

/* ───────────────────────── Types ───────────────────────── */

export type { Position } from "@/types";

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
  reset: () => void;

  // Fetch
  loadAll: (symbols?: string[]) => Promise<void>;
  loadDailyRPnL: () => Promise<void>;

  // Aggregations (локальные вычисления)
  totalExposureUSD: (getMarkPrice: (symbol: string) => number | undefined) => number;
  totalUPnL: (getMarkPrice: (symbol: string) => number | undefined) => number;
  totalRPnL: () => number;
};

/* ─────────────────────── Helpers ─────────────────────── */

const norm = (s: string): string => (s || "").trim().toUpperCase();

const parseNum = (v: unknown): number | undefined => {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
};

function pickNum(obj: Record<string, unknown>, key: string): number | undefined {
  return parseNum(obj?.[key]);
}

/** Normalize a position object to canonical keys and uppercase symbol. */
function normalizePosition(p: Position): Position | null {
  const symbol = norm(p.symbol);
  if (!symbol) return null;

  // qty обязателен → число, иначе 0
  const qty = parseNum((p as Record<string, unknown>).qty) ?? 0;

  // avg_price: поддерживаем возможные ключи с бэка (avg_price предпочтителен)
  const avg =
    pickNum(p as Record<string, unknown>, "avg_price") ??
    pickNum(p as Record<string, unknown>, "avg");

  // realized_pnl: поддерживаем альтернативные ключи
  const rpnl =
    pickNum(p as Record<string, unknown>, "realized_pnl") ??
    pickNum(p as Record<string, unknown>, "rpnl") ??
    pickNum(p as Record<string, unknown>, "realized_usd");

  return {
    ...p,
    symbol,
    qty,
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
  const m = parseNum(mark);
  if (m === undefined) return 0;
  const qty = parseNum((p as Record<string, unknown>).qty) ?? 0;
  const avg = pickNum(p as Record<string, unknown>, "avg_price");
  if (avg === undefined) return 0;
  return (m - avg) * qty;
}

const defaultPnlParams: PnlParams = {
  period: "today",
  tz: null,
  from: null,
  to: null,
};

function providerReady(): boolean {
  const ps = useProvider.getState();
  return !!ps.active && !!ps.mode; // можно добавить проверку wsEnabled/revision при желании
}

function dedupeSymbols(list: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const s of list) {
    const v = norm(s);
    if (v && !seen.has(v)) {
      seen.add(v);
      out.push(v);
    }
  }
  return out;
}

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

      const data: { total_usd?: number | string } = await res.json();
      const total = parseNum(data?.total_usd) ?? 0;
      set({ pnlSummary: total });
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

  reset: () => {
    set({
      positionsBySymbol: {},
      loading: false,
      error: null,
      dailyRPnL: null,
      loadingDaily: false,
      errorDaily: null,
      pnlSummary: null,
      pnlLoading: false,
      pnlError: null,
      pnlParams: defaultPnlParams,
    });
  },

  loadAll: async (symbols?: string[]) => {
    try {
      // 🔒 не бомбим API, если провайдер ещё не готов (устраняет 500 на старте)
      if (!providerReady()) {
        console.warn("🔒 [positions] Provider not ready, skipping loadAll");
        return;
      }

      set({ loading: true, error: null });

      let qs = "";
      if (symbols && symbols.length > 0) {
        const params = new URLSearchParams();
        for (const s of dedupeSymbols(symbols)) params.append("symbols", s);
        qs = `?${params.toString()}`;
      }

      console.log("📡 [positions] Fetching /api/exec/positions" + qs);
      
      const res = await fetch(`/api/exec/positions${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });

      if (!res.ok) {
        const text = await res.text();
        const errorMsg = `GET /api/exec/positions failed: ${res.status} ${text}`;
        console.error("❌ [positions]", errorMsg);
        set({ error: errorMsg });
        return;
      }

      const data: unknown = await res.json();
      const list = Array.isArray(data)
        ? (data.filter((x) => x && typeof x === "object") as Position[])
        : [];

      console.log("✅ [positions] Loaded", list.length, "positions:", list);

      set({ positionsBySymbol: toMap(list) });
      
      console.log("✅ [positions] Store updated, positionsBySymbol:", get().positionsBySymbol);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Failed to load positions";
      console.error("❌ [positions] Exception:", errorMsg);
      set({ error: errorMsg });
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
        set({ errorDaily: `GET /api/pnl/summary failed: ${res.status} ${text}` });
        return;
      }
      const data: { total_usd?: number | string } = await res.json();
      set({ dailyRPnL: parseNum(data?.total_usd) ?? 0 });
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
      const m = parseNum(mark);
      const q = parseNum((p as Record<string, unknown>).qty) ?? 0;
      if (m !== undefined) total += Math.abs(q) * m;
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
    // обратная совместимость — суммируем из позиций
    const map = get().positionsBySymbol;
    let total = 0;
    for (const p of Object.values(map)) {
      const r =
        pickNum(p as Record<string, unknown>, "realized_pnl") ??
        pickNum(p as Record<string, unknown>, "rpnl") ??
        pickNum(p as Record<string, unknown>, "realized_usd");
      if (r !== undefined) total += r;
    }
    return total;
  },
}));

/* Back-compat alias to match older imports like `usePositions` */
export const usePositions = usePositionsStore;

/* ───────────────────── Selectors ───────────────────── */

export const selectPositionsArray = (s: PositionsState): Position[] =>
  Object.values(s.positionsBySymbol);

export const selectActivePositionsArray = (s: PositionsState): Position[] =>
  Object.values(s.positionsBySymbol).filter(
    (p) => Number.isFinite((p as Record<string, unknown>).qty as number) && (p as Record<string, unknown>).qty !== 0
  );

export const selectBySymbol =
  (symbol: string) =>
  (s: PositionsState): Position | undefined =>
    s.positionsBySymbol[norm(symbol)];
