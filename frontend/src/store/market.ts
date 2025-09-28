// src/store/market.ts
import { create } from "zustand";
import type { Quote, Position, Level } from "@/types/api";
import { normalizeSymbol } from "@/utils/format";

/** Quote shape we keep in the store (extends backend Quote with optional fields) */
export type StoreQuote = Quote & {
  ts?: number;
  ts_ms?: number;
  bidQty?: number;
  askQty?: number;
  imbalance?: number;
  mid?: number;
  spread_bps?: number;
  bids?: Level[];
  asks?: Level[];
};

type QuotesMap = Record<string, StoreQuote>;
export type TapeItem = { ts: number; mid: number; spread_bps: number };

type WithTs = { ts?: number; ts_ms?: number };

type MarketState = {
  quotes: QuotesMap;
  tape: Record<string, TapeItem[]>;
  positions: Record<string, Position>;

  /** меняется при любом осмысленном обновлении котировок; используем как лёгкий триггер */
  quotesTick: number;

  clear: () => void;
  setPositions: (ps: Position[]) => void;
  upsertPosition: (p: Position) => void;
  removePosition: (symbol: string) => void;

  applySnapshot: (qs: StoreQuote[]) => void;
  applyQuotes: (qs: StoreQuote[]) => void;

  ingest: (qs: StoreQuote[]) => void;

  quoteOf: (symbol: string) => StoreQuote | undefined;
  tapeOf: (symbol: string) => TapeItem[];
};

const MAX_TAPE = 50;
const L2_TOP = 10;
const SPREAD_BPS_MAX = 20_000;

/* ───────── utils ───────── */
const fnum = (v: unknown, dflt = 0) =>
  typeof v === "number" && Number.isFinite(v) ? v : Number(v ?? dflt);

const posNum = (v: unknown) => {
  const x = fnum(v, 0);
  return x > 0 ? x : 0;
};

const nonNeg = (v: unknown) => {
  const x = fnum(v, 0);
  return x >= 0 ? x : 0;
};

// нормализация символа (общая утилита)
const normSym = (s: unknown) => normalizeSymbol(String(s || ""));

const tsOrNow = (w: WithTs, now: number): number => {
  const t = w.ts ?? w.ts_ms ?? now;
  return Number.isFinite(t) && t > 0 ? t : now;
};

function getNumField(obj: unknown, key: string): number | undefined {
  if (obj && typeof obj === "object") {
    const v = (obj as Record<string, unknown>)[key];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return undefined;
}

function sanitizePosition(p: Position): Position | undefined {
  const symbol = normSym(p.symbol);
  const qty = fnum(p.qty, 0);
  const resolved_avg_price =
    getNumField(p, "avg_price") ?? getNumField(p, "avg") ?? 0;
  const resolved_unrealized =
    getNumField(p, "unrealized_pnl") ?? getNumField(p, "upnl") ?? 0;
  const resolved_realized =
    getNumField(p, "realized_pnl") ?? getNumField(p, "rpnl") ?? 0;
  const resolved_ts_ms = getNumField(p, "ts_ms") ?? getNumField(p, "ts");
  const resolved_ts = getNumField(p, "ts") ?? resolved_ts_ms;

  if (qty === 0) return undefined;

  const out: Position = {
    symbol,
    qty,
    avg_price: nonNeg(resolved_avg_price),
    unrealized_pnl: fnum(resolved_unrealized, 0),
    realized_pnl: fnum(resolved_realized, 0),
    ts_ms: resolved_ts_ms,
    avg: nonNeg(resolved_avg_price),
    upnl: fnum(resolved_unrealized, 0),
    rpnl: fnum(resolved_realized, 0),
    ts: resolved_ts,
  };
  return out;
}

function sanitizeLevels(
  levels: ReadonlyArray<readonly [number, number]> | undefined,
  side: "bid" | "ask",
  keep: number
): Level[] {
  if (!levels || !levels.length) return [];
  const out: Level[] = [];
  for (let i = 0; i < levels.length; i++) {
    const lv = levels[i];
    const p = Number(lv?.[0]);
    const q = Number(lv?.[1]);
    if (Number.isFinite(p) && p > 0 && Number.isFinite(q) && q > 0) {
      out.push([p, q]);
    }
  }
  out.sort((a, b) => (side === "bid" ? b[0] - a[0] : a[0] - b[0]));
  return out.slice(0, Math.max(1, keep));
}

function normalizeQuote(input: StoreQuote): StoreQuote {
  const sym = normSym(input.symbol);

  let bid = posNum(input.bid);
  let ask = posNum(input.ask);

  const bidsL2 = sanitizeLevels(input.bids, "bid", L2_TOP);
  const asksL2 = sanitizeLevels(input.asks, "ask", L2_TOP);

  if (!(bid > 0) && bidsL2.length) bid = bidsL2[0][0];
  if (!(ask > 0) && asksL2.length) ask = asksL2[0][0];

  const mid =
    posNum(input.mid) > 0
      ? (input.mid as number)
      : bid > 0 && ask > 0
      ? (bid + ask) / 2
      : bid > 0
      ? bid
      : ask > 0
      ? ask
      : 0;

  let spread_bps: number =
    typeof input.spread_bps === "number" &&
    Number.isFinite(input.spread_bps) &&
    input.spread_bps >= 0
      ? input.spread_bps
      : mid > 0 && bid > 0 && ask > 0
      ? ((ask - bid) / mid) * 10_000
      : 0;
  spread_bps = Math.max(0, Math.min(SPREAD_BPS_MAX, spread_bps));

  const bidQty = posNum(input.bidQty);
  const askQty = posNum(input.askQty);

  const hasImb =
    typeof input.imbalance === "number" && Number.isFinite(input.imbalance);
  const denom = bidQty + askQty;
  const imbalance =
    hasImb ? (input.imbalance as number) : denom > 0 ? bidQty / denom : undefined;

  const bids = bidsL2.length ? (bidsL2 as Level[]) : undefined;
  const asks = asksL2.length ? (asksL2 as Level[]) : undefined;

  return {
    ...input,
    symbol: sym,
    bid,
    ask,
    bidQty,
    askQty,
    mid,
    spread_bps,
    bids,
    asks,
    ...(imbalance !== undefined ? { imbalance } : {}),
  };
}

const isMeaningfulQuote = (q: StoreQuote): boolean => {
  const hasL1Both = (q.bid ?? 0) > 0 && (q.ask ?? 0) > 0;
  const hasMid = (q.mid ?? 0) > 0;
  const hasL2 = (q.bids?.length ?? 0) > 0 || (q.asks?.length ?? 0) > 0;
  const hasQty = (q.bidQty ?? 0) > 0 || (q.askQty ?? 0) > 0;
  return hasL1Both || hasMid || hasL2 || hasQty;
};

/* ───────── store ───────── */
export const useMarket = create<MarketState>((set, get) => ({
  quotes: {},
  tape: {},
  positions: {},
  quotesTick: 0,

  clear: () => set({ quotes: {}, tape: {}, positions: {}, quotesTick: 0 }),

  setPositions: (ps) =>
    set(() => {
      const positions: Record<string, Position> = {};
      for (const p of ps ?? []) {
        const sp = sanitizePosition(p);
        if (sp) positions[normSym(sp.symbol)] = sp;
      }
      return { positions };
    }),

  upsertPosition: (p) =>
    set((s) => {
      const map = { ...s.positions };
      const sp = sanitizePosition(p);
      const sym = normSym(p.symbol);
      if (sp) {
        map[sym] = sp;
      } else if (sym in map) {
        delete map[sym];
      }
      return { positions: map };
    }),

  removePosition: (symbol) =>
    set((s) => {
      const map = { ...s.positions };
      const sym = normSym(symbol);
      if (sym in map) delete map[sym];
      return { positions: map };
    }),

  // ⬇️ IMPORTANT: do NOT wipe state on empty snapshots; merge instead
  applySnapshot: (qs) =>
    set((s) => {
      if (!qs?.length) return {}; // keep existing quotes/tape to avoid flicker
      const quotes: QuotesMap = { ...s.quotes };
      const tape: Record<string, TapeItem[]> = { ...s.tape };
      const now = Date.now();
      let changedAny = false;

      for (const raw of qs) {
        const q = normalizeQuote(raw);
        if (!isMeaningfulQuote(q)) continue;

        const sym = q.symbol;
        const prev = quotes[sym];

        // merge like applyQuotes, but also (re)seed tape if empty
        const merged: StoreQuote = {
          ...(prev || { symbol: sym }),
          ...q,
          bid: q.bid > 0 ? q.bid : prev?.bid,
          ask: q.ask > 0 ? q.ask : prev?.ask,
          bidQty: q.bidQty && q.bidQty > 0 ? q.bidQty : prev?.bidQty,
          askQty: q.askQty && q.askQty > 0 ? q.askQty : prev?.askQty,
          mid: q.mid && q.mid > 0 ? q.mid : prev?.mid,
          spread_bps:
            typeof q.spread_bps === "number" && q.spread_bps >= 0
              ? q.spread_bps
              : prev?.spread_bps,
          bids: q.bids?.length ? q.bids : prev?.bids,
          asks: q.asks?.length ? q.asks : prev?.asks,
          imbalance: typeof q.imbalance === "number" ? q.imbalance : prev?.imbalance,
        };

        const changed =
          !prev ||
          merged.bid !== prev.bid ||
          merged.ask !== prev.ask ||
          merged.mid !== prev.mid ||
          merged.spread_bps !== prev.spread_bps ||
          merged.bidQty !== prev.bidQty ||
          merged.askQty !== prev.askQty ||
          merged.imbalance !== prev.imbalance ||
          merged.bids !== prev.bids ||
          merged.asks !== prev.asks;

        if (changed) {
          quotes[sym] = merged;
          changedAny = true;
        }

        let time = tsOrNow(q, now);
        const arr = tape[sym] ? [...tape[sym]] : [];
        const last = arr[arr.length - 1];
        if (last && time <= last.ts) time = last.ts + 1;
        const midVal = merged.mid ?? 0;
        const sbps = merged.spread_bps ?? 0;

        if (!last || last.mid !== midVal || last.spread_bps !== sbps) {
          arr.push({ ts: time, mid: midVal, spread_bps: sbps });
          if (arr.length > MAX_TAPE) arr.splice(0, arr.length - MAX_TAPE);
          tape[sym] = arr;
          changedAny = true;
        }
      }

      return changedAny ? { quotes, tape, quotesTick: Date.now() } : {};
    }),

  applyQuotes: (qs) =>
    set((s) => {
      if (!qs?.length) return {};
      const quotes = { ...s.quotes };
      const tape = { ...s.tape };
      const now = Date.now();
      let changedAny = false;

      for (const raw of qs) {
        const qn = normalizeQuote(raw);
        if (!isMeaningfulQuote(qn)) continue;

        const sym = qn.symbol;
        const prev = quotes[sym];

        const merged: StoreQuote = {
          ...(prev || { symbol: sym }),
          ...qn,
          bid: qn.bid > 0 ? qn.bid : prev?.bid,
          ask: qn.ask > 0 ? qn.ask : prev?.ask,
          bidQty: qn.bidQty && qn.bidQty > 0 ? qn.bidQty : prev?.bidQty,
          askQty: qn.askQty && qn.askQty > 0 ? qn.askQty : prev?.askQty,
          mid: qn.mid && qn.mid > 0 ? qn.mid : prev?.mid,
          spread_bps:
            typeof qn.spread_bps === "number" && qn.spread_bps >= 0
              ? qn.spread_bps
              : prev?.spread_bps,
          bids: qn.bids?.length ? qn.bids : prev?.bids,
          asks: qn.asks?.length ? qn.asks : prev?.asks,
          imbalance: typeof qn.imbalance === "number" ? qn.imbalance : prev?.imbalance,
        };

        const changed =
          !prev ||
          merged.bid !== prev.bid ||
          merged.ask !== prev.ask ||
          merged.mid !== prev.mid ||
          merged.spread_bps !== prev.spread_bps ||
          merged.bidQty !== prev.bidQty ||
          merged.askQty !== prev.askQty ||
          merged.imbalance !== prev.imbalance ||
          merged.bids !== prev.bids ||
          merged.asks !== prev.asks;

        if (changed) {
          quotes[sym] = merged;
          changedAny = true;
        }

        let time = tsOrNow(qn, now);
        const arr = tape[sym] ? [...tape[sym]] : [];
        const last = arr[arr.length - 1];
        if (last && time <= last.ts) time = last.ts + 1;

        const midVal = merged.mid ?? 0;
        const sbps = merged.spread_bps ?? 0;
        if (!last || last.mid !== midVal || last.spread_bps !== sbps) {
          arr.push({ ts: time, mid: midVal, spread_bps: sbps });
          changedAny = true;
        }
        if (arr.length > MAX_TAPE) arr.splice(0, arr.length - MAX_TAPE);
        tape[sym] = arr;
      }
      return changedAny ? { quotes, tape, quotesTick: Date.now() } : { quotes, tape };
    }),

  ingest: (qs) => get().applyQuotes(qs),

  quoteOf: (symbol) => get().quotes[normSym(symbol)],
  tapeOf: (symbol) => get().tape[normSym(symbol)] ?? [],
}));

declare global {
  interface Window {
    __useMarket?: typeof useMarket;
  }
}
if (typeof window !== "undefined") {
  window.__useMarket = useMarket;
}
