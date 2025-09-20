// src/store/metrics.ts
import { create } from "zustand";
import type { StrategyMetricsJSON } from "@/types/api";

type MetricsState = {
  snapshot: StrategyMetricsJSON | null;
  setSnapshot: (m: StrategyMetricsJSON | null | undefined) => void;

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

// Narrow “signature” of the snapshot for cheap equality checks
function makeSignature(m: StrategyMetricsJSON): string {
  const parts: string[] = [];

  const pushMap = (name: string, map?: Record<string, number>) => {
    if (!map) return;
    const keys = Object.keys(map).sort();
    for (const k of keys) parts.push(`${name}:${k}=${map[k] ?? 0}`);
  };

  pushMap("e", m.entries);

  // exits — nested: { [sym]: { TP, SL, TIMEOUT } }
  if (m.exits) {
    const syms = Object.keys(m.exits).sort();
    for (const s of syms) {
      const ex: Partial<Record<"TP" | "SL" | "TIMEOUT", number>> = m.exits[s] ?? {};
      parts.push(`x:${s}:TP=${ex.TP ?? 0}`);
      parts.push(`x:${s}:SL=${ex.SL ?? 0}`);
      parts.push(`x:${s}:TO=${ex.TIMEOUT ?? 0}`);
    }
  }

  pushMap("o", m.open_positions);
  pushMap("r", m.realized_pnl);

  return parts.join("|");
}

export const useMetrics = create<MetricsState>((set, get) => ({
  snapshot: null,

  setSnapshot: (m) => {
    if (!m) return; // ignore empty payloads safely
    const prev = get().snapshot;
    if (!prev) {
      set({ snapshot: m });
      return;
    }
    try {
      const same = makeSignature(prev) === makeSignature(m);
      if (same) return;
    } catch {
      // if anything goes wrong, just update
    }
    set({ snapshot: m });
  },

  // Primitive selectors
  entriesOf: (sym) => get().snapshot?.entries?.[sym] ?? 0,

  exitsTPOf: (sym) => get().snapshot?.exits?.[sym]?.TP ?? 0,
  exitsSLOf: (sym) => get().snapshot?.exits?.[sym]?.SL ?? 0,
  exitsTIMEOUTOf: (sym) => get().snapshot?.exits?.[sym]?.TIMEOUT ?? 0,

  openFlagOf: (sym) => get().snapshot?.open_positions?.[sym] ?? 0,
  realizedOf: (sym) => get().snapshot?.realized_pnl?.[sym] ?? 0,

  // Compatibility (returns a new object — prefer numeric selectors above)
  exitsOf: (sym) => {
    const e = get().snapshot?.exits?.[sym] ?? {};
    return {
      TP: e.TP ?? 0,
      SL: e.SL ?? 0,
      TIMEOUT: e.TIMEOUT ?? 0,
    };
  },
}));
