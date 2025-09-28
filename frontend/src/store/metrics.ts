import { create } from "zustand";
import type { StrategyMetricsJSON } from "@/types/api";

type MetricsState = {
  snapshot: StrategyMetricsJSON | null;
  /** internal: last signature for cheap equality */
  _sig?: string;

  setSnapshot: (m: StrategyMetricsJSON | null | undefined) => void;
  clear: () => void;

  // Primitive selectors (numbers)
  entriesOf: (sym: string) => number;
  exitsTPOf: (sym: string) => number;
  exitsSLOf: (sym: string) => number;
  exitsTIMEOUTOf: (sym: string) => number;
  openFlagOf: (sym: string) => number;
  realizedOf: (sym: string) => number;

  // Compatibility (prefer numeric selectors above)
  exitsOf: (sym: string) => { TP: number; SL: number; TIMEOUT: number };
};

// ───────────────── helpers ─────────────────
const normKey = (s: string) => s.trim().toUpperCase();
const fnum = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v) ? v : 0;

function normalizeSnapshot(raw: StrategyMetricsJSON): StrategyMetricsJSON {
  // entries
  const entries: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw.entries ?? {})) {
    entries[normKey(k)] = fnum(v);
  }

  // exits nested map
  const exits: Record<string, { TP?: number; SL?: number; TIMEOUT?: number }> = {};
  for (const [k, obj] of Object.entries(raw.exits ?? {})) {
    const K = normKey(k);
    const TP = fnum((obj ?? {}).TP);
    const SL = fnum((obj ?? {}).SL);
    const TIMEOUT = fnum((obj ?? {}).TIMEOUT);
    exits[K] = { TP, SL, TIMEOUT };
  }

  // open positions
  const open_positions: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw.open_positions ?? {})) {
    open_positions[normKey(k)] = fnum(v);
  }

  // realized pnl
  const realized_pnl: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw.realized_pnl ?? {})) {
    realized_pnl[normKey(k)] = fnum(v);
  }

  return { entries, exits, open_positions, realized_pnl };
}

function makeSignature(m: StrategyMetricsJSON): string {
  const parts: string[] = [];

  const pushMap = (name: string, map?: Record<string, number>) => {
    if (!map) return;
    const keys = Object.keys(map).sort();
    for (const k of keys) parts.push(`${name}:${k}=${map[k] ?? 0}`);
  };

  pushMap("e", m.entries);

  if (m.exits) {
    const syms = Object.keys(m.exits).sort();
    for (const s of syms) {
      const ex = m.exits[s] ?? {};
      parts.push(`x:${s}:TP=${ex.TP ?? 0}`);
      parts.push(`x:${s}:SL=${ex.SL ?? 0}`);
      parts.push(`x:${s}:TO=${ex.TIMEOUT ?? 0}`);
    }
  }

  pushMap("o", m.open_positions);
  pushMap("r", m.realized_pnl);

  return parts.join("|");
}

// ───────────────── store ─────────────────
export const useMetrics = create<MetricsState>((set, get) => ({
  snapshot: null,
  _sig: undefined,

  setSnapshot: (m) => {
    if (!m) return; // ignore empty payloads safely
    const next = normalizeSnapshot(m);
    const sig = makeSignature(next);

    const prevSig = get()._sig;
    if (prevSig === sig) return; // no changes → no update

    set({ snapshot: next, _sig: sig });
  },

  clear: () => set({ snapshot: null, _sig: undefined }),

  // selectors
  entriesOf: (sym) => get().snapshot?.entries?.[normKey(sym)] ?? 0,
  exitsTPOf: (sym) => get().snapshot?.exits?.[normKey(sym)]?.TP ?? 0,
  exitsSLOf: (sym) => get().snapshot?.exits?.[normKey(sym)]?.SL ?? 0,
  exitsTIMEOUTOf: (sym) => get().snapshot?.exits?.[normKey(sym)]?.TIMEOUT ?? 0,
  openFlagOf: (sym) => get().snapshot?.open_positions?.[normKey(sym)] ?? 0,
  realizedOf: (sym) => get().snapshot?.realized_pnl?.[normKey(sym)] ?? 0,

  // compatibility
  exitsOf: (sym) => {
    const e = get().snapshot?.exits?.[normKey(sym)] ?? {};
    return { TP: e.TP ?? 0, SL: e.SL ?? 0, TIMEOUT: e.TIMEOUT ?? 0 };
  },
}));
