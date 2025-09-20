// src/store/market.ts
import { create } from "zustand";
import type { Quote, Position, Level } from "@/types/api";

/** Quote shape we keep in the store (extends backend Quote with optional fields) */
export type StoreQuote = Quote & {
  ts?: number;    // ms
  ts_ms?: number; // legacy
  bidQty?: number;
  askQty?: number;
};

type QuotesMap = Record<string, StoreQuote>;
export type TapeItem = { ts: number; mid: number; spread_bps: number };

/** Support ts and ts_ms for time extraction */
type WithTs = { ts?: number; ts_ms?: number };

type MarketState = {
  quotes: QuotesMap;
  tape: Record<string, TapeItem[]>;
  positions: Record<string, Position>;

  /** reducers */
  setPositions: (ps: Position[]) => void;
  applySnapshot: (qs: StoreQuote[]) => void;
  applyQuotes: (qs: StoreQuote[]) => void;

  /** optional helpers/selectors */
  quoteOf: (symbol: string) => StoreQuote | undefined;
  tapeOf: (symbol: string) => TapeItem[];
};

const MAX_TAPE = 50;
const L2_TOP = 10;                 // держим максимум 10 уровней по стороне
const SPREAD_BPS_MAX = 20_000;     // 200% потолок для безопасности

const fnum = (v: unknown, dflt = 0) =>
  typeof v === "number" && Number.isFinite(v) ? v : dflt;

const posNum = (v: unknown) => {
  const x = fnum(v, 0);
  return x > 0 ? x : 0;
};

const normSym = (s: unknown) => String(s || "").trim().toUpperCase();

const tsOrNow = (w: WithTs, now: number): number => {
  const t = w.ts ?? w.ts_ms ?? now;
  return Number.isFinite(t) && t > 0 ? t : now;
};

/** Санитация уровней стакана + сортировка + клип до TOP */
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

/** Ensure mid/spread_bps present; derive L1 из L2 при необходимости; нормализуем L2 */
function normalizeQuote(input: StoreQuote): StoreQuote {
  const sym = normSym(input.symbol);

  // исходные поля
  let bid = posNum(input.bid);
  let ask = posNum(input.ask);

  // L2 уровни: очищаем/сортируем/клипуем
  const bidsL2 = sanitizeLevels(input.bids, "bid", L2_TOP);
  const asksL2 = sanitizeLevels(input.asks, "ask", L2_TOP);

  // если L1 нет — попробуем взять из L2
  if (!(bid > 0) && bidsL2.length) bid = bidsL2[0][0];
  if (!(ask > 0) && asksL2.length) ask = asksL2[0][0];

  // mid
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

  // spread_bps (с клипом верха)
  let spread_bps: number =
    typeof input.spread_bps === "number" &&
    Number.isFinite(input.spread_bps) &&
    input.spread_bps >= 0
      ? input.spread_bps
      : mid > 0 && bid > 0 && ask > 0
      ? ((ask - bid) / mid) * 10_000
      : 0;
  spread_bps = Math.max(0, Math.min(SPREAD_BPS_MAX, spread_bps));

  // qtys (опционально)
  const bidQty = posNum(input.bidQty);
  const askQty = posNum(input.askQty);

  // чтобы не плодить пустые массивы в состоянии — отдаем undefined, если нет уровней
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
  };
}

/** Считаем котировку «содержательной», если есть хоть что-то ненулевое */
const isMeaningfulQuote = (q: StoreQuote): boolean => {
  const hasL1Both = (q.bid ?? 0) > 0 && (q.ask ?? 0) > 0;
  const hasMid = (q.mid ?? 0) > 0;
  const hasL2 = (q.bids?.length ?? 0) > 0 || (q.asks?.length ?? 0) > 0;
  const hasQty = (q.bidQty ?? 0) > 0 || (q.askQty ?? 0) > 0;
  return hasL1Both || hasMid || hasL2 || hasQty;
};

export const useMarket = create<MarketState>((set, get) => ({
  quotes: {},
  tape: {},
  positions: {},

  setPositions: (ps) =>
    set((s) => {
      const map = { ...s.positions };
      for (const p of ps) {
        map[normSym(p.symbol)] = p;
      }
      return { positions: map };
    }),

  applySnapshot: (qs) =>
    set(() => {
      const quotes: QuotesMap = {};
      const tape: Record<string, TapeItem[]> = {};
      const now = Date.now();

      for (const raw of qs) {
        const q = normalizeQuote(raw);
        if (!isMeaningfulQuote(q)) {
          // игнорируем нулевые «заглушки», не засоряем карусель значением 0
          continue;
        }
        quotes[q.symbol] = q;

        const time = tsOrNow(q, now);
        tape[q.symbol] = [{ ts: time, mid: q.mid ?? 0, spread_bps: q.spread_bps ?? 0 }];
      }
      return { quotes, tape };
    }),

  applyQuotes: (qs) =>
    set((s) => {
      if (!qs?.length) return {};
      const quotes = { ...s.quotes };
      const tape = { ...s.tape };
      const now = Date.now();

      for (const raw of qs) {
        const q = normalizeQuote(raw);

        // если апдейт «пустой» — не затираем ранее хорошие данные и не пушим в ленту
        if (!isMeaningfulQuote(q)) {
          continue;
        }

        quotes[q.symbol] = q;

        // time extraction + защита от регрессии времени
        let time = tsOrNow(q, now);

        const arr = tape[q.symbol] ? [...tape[q.symbol]] : [];

        // если пришёл ts <= последнего — подвинем на +1ms
        const last = arr[arr.length - 1];
        if (last && time <= last.ts) time = last.ts + 1;

        // дедуп по значению для ленты
        const midVal = q.mid ?? 0;
        const sbps = q.spread_bps ?? 0;
        if (!last || last.mid !== midVal || last.spread_bps !== sbps) {
          arr.push({ ts: time, mid: midVal, spread_bps: sbps });
        }

        // обрезка истории
        if (arr.length > MAX_TAPE) arr.splice(0, arr.length - MAX_TAPE);
        tape[q.symbol] = arr;
      }
      return { quotes, tape };
    }),

  // handy selectors
  quoteOf: (symbol) => get().quotes[normSym(symbol)],
  tapeOf: (symbol) => get().tape[normSym(symbol)] ?? [],
}));

// --- Dev: expose the store to the browser console for debugging ---
declare global {
  interface Window {
    __useMarket?: typeof useMarket;
  }
}
if (typeof window !== "undefined") {
  window.__useMarket = useMarket;
}
