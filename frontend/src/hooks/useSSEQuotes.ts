import { useEffect, useMemo, useCallback } from "react";
import { openSSE } from "@/lib/sse";
import { useMarket } from "@/store/market";
import type { Quote, Level } from "@/types/api";

/** Ext quote we keep in the store */
type ExtQuote = Quote & {
  ts: number; // ms
  bidQty?: number;
  askQty?: number;
  mid?: number;
  spread_bps?: number;
  bids?: Level[];
  asks?: Level[];
};

type IncomingLevel =
  | [number, number]
  | { price: number; qty?: number; quantity?: number };

type IncomingQuote = {
  symbol?: string;
  bid?: number;
  ask?: number;
  ts?: number;
  ts_ms?: number;
  bidQty?: number;
  bid_qty?: number;
  askQty?: number;
  ask_qty?: number;
  mid?: number;
  spread_bps?: number;
  bids?: IncomingLevel[];
  asks?: IncomingLevel[];
};

type QuotesEnvelopeDirect = { quotes?: IncomingQuote[]; type?: string };
type QuotesEnvelopeNested = { data?: { quotes?: IncomingQuote[] }; type?: string };
type QuotesEnvelope = QuotesEnvelopeDirect | QuotesEnvelopeNested;

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}
function isIncomingQuoteArray(x: unknown): x is IncomingQuote[] {
  return Array.isArray(x);
}

function extractQuotes(payload: unknown): IncomingQuote[] {
  if (isIncomingQuoteArray(payload)) return payload;
  if (!isObject(payload)) return [];
  const direct = (payload as QuotesEnvelopeDirect).quotes;
  if (isIncomingQuoteArray(direct)) return direct;
  const nested = (payload as QuotesEnvelopeNested).data?.quotes;
  if (isIncomingQuoteArray(nested)) return nested;
  return [];
}

function toLevelTuple(x: IncomingLevel | undefined): Level | null {
  if (!x) return null;
  if (Array.isArray(x)) {
    const [p, q] = x;
    return typeof p === "number" && typeof q === "number" ? ([p, q] as const) : null;
  }
  const p = x.price;
  const q =
    typeof x.qty === "number"
      ? x.qty
      : typeof x.quantity === "number"
      ? x.quantity
      : undefined;
  return typeof p === "number" && typeof q === "number" ? ([p, q] as const) : null;
}

function normalizeQuotes(payload: unknown, dbgTag: string): ExtQuote[] {
  const rawArr = extractQuotes(payload);
  if (!rawArr.length) return [];

  const out: ExtQuote[] = [];

  for (const q of rawArr) {
    if (!q?.symbol) continue;

    const bid = typeof q.bid === "number" && q.bid > 0 ? q.bid : 0;
    const ask = typeof q.ask === "number" && q.ask > 0 ? q.ask : 0;

    const hasL2 =
      (Array.isArray(q.bids) && q.bids.length > 0) ||
      (Array.isArray(q.asks) && q.asks.length > 0);
    const hasL1 = bid > 0 || ask > 0;

    if (!hasL1 && !hasL2) {
      if (import.meta.env.DEV) {
        console.debug(
          `[SSE/drop] ${dbgTag} ${q.symbol} empty frame (bid=0 & ask=0, no L2, ts=${q.ts ?? q.ts_ms ?? "?"})`
        );
      }
      continue;
    }

    const tsRaw =
      typeof q.ts === "number"
        ? q.ts
        : typeof q.ts_ms === "number"
        ? q.ts_ms
        : NaN;
    const ts = Number.isFinite(tsRaw) && tsRaw > 0 ? tsRaw : Date.now();

    const mid =
      typeof q.mid === "number"
        ? q.mid
        : bid > 0 && ask > 0
        ? (bid + ask) / 2
        : bid || ask || 0;

    const spread_bps =
      typeof q.spread_bps === "number" && q.spread_bps >= 0
        ? q.spread_bps
        : mid > 0 && bid > 0 && ask > 0
        ? ((ask - bid) / mid) * 10_000
        : 0;

    const bidQty =
      typeof q.bidQty === "number"
        ? q.bidQty
        : typeof q.bid_qty === "number"
        ? q.bid_qty
        : undefined;

    const askQty =
      typeof q.askQty === "number"
        ? q.askQty
        : typeof q.ask_qty === "number"
        ? q.ask_qty
        : undefined;

    const bids: Level[] | undefined = Array.isArray(q.bids)
      ? q.bids.map(toLevelTuple).filter((lv): lv is Level => lv !== null)
      : undefined;

    const asks: Level[] | undefined = Array.isArray(q.asks)
      ? q.asks.map(toLevelTuple).filter((lv): lv is Level => lv !== null)
      : undefined;

    out.push({
      symbol: q.symbol,
      bid,
      ask,
      ts,
      mid,
      spread_bps,
      bidQty,
      askQty,
      bids,
      asks,
    });
  }

  return out;
}

export function useSSEQuotes(symbols: string[]) {
  const applySnapshot = useMarket((s) => s.applySnapshot);
  const applyQuotes = useMarket((s) => s.applyQuotes);

  // Normalize & stabilize symbol list
  const joined = useMemo(() => {
    const set = new Set(
      symbols
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean)
    );
    return Array.from(set).sort().join(",");
  }, [symbols]);

  const intervalEnv =
    (import.meta.env.VITE_SSE_INTERVAL_MS as string | undefined) ?? "";
  const intervalMs = Number.isFinite(Number.parseInt(intervalEnv, 10))
    ? Number.parseInt(intervalEnv, 10)
    : 500;

  const base =
    ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "").trim() ||
    "http://localhost:8000";

  const handleMessage = useCallback(
    (e: MessageEvent<string>) => {
      try {
        const payload: unknown = JSON.parse(e.data);
        const payloadType =
          isObject(payload) && typeof (payload as QuotesEnvelope).type === "string"
            ? (payload as QuotesEnvelope).type
            : undefined;

        const eventName = e.type !== "message" ? e.type : payloadType;
        const dbgTag = eventName ?? "message";

        if (eventName === "snapshot") {
          const quotes = normalizeQuotes(payload, dbgTag);
          if (quotes.length) applySnapshot(quotes);
          return;
        }

        if (eventName === "quotes" || eventName === "message" || eventName === undefined) {
          const quotes = normalizeQuotes(payload, dbgTag);
          if (quotes.length) applyQuotes(quotes);
          return;
        }
        // ignore pings/others
      } catch {
        // ignore malformed frames
      }
    },
    [applySnapshot, applyQuotes]
  );

  useEffect(() => {
    if (!joined) {
      if (import.meta.env.DEV) console.debug("[SSE] skip (no symbols)");
      return;
    }

    const u = new URL("/api/market/stream", base);
    u.searchParams.set("symbols", joined);
    u.searchParams.set("interval_ms", String(intervalMs));

    if (import.meta.env.DEV) console.debug("[SSE] connect", u.toString());

    const dispose = openSSE(u.toString(), handleMessage, [
      "snapshot",
      "quotes",
      "ping",
      "message",
    ]);

    return () => {
      if (import.meta.env.DEV) console.debug("[SSE] close");
      dispose();
    };
  }, [joined, intervalMs, base, handleMessage]);
}
