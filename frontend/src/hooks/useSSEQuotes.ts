// src/hooks/useSSEQuotes.ts
import { useEffect, useMemo, useCallback } from "react";
import { openMarketStream } from "@/lib/sse";
import { useMarket } from "@/store/market";
import type { Quote, Level } from "@/types/api";

/** Ext quote kept in the store */
type ExtQuote = Quote & {
  ts: number; // ms
  bidQty?: number;
  askQty?: number;
  mid?: number;
  spread_bps?: number;
  bids?: Level[];
  asks?: Level[];
  imbalance?: number;
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
  imbalance?: number;
  bids?: IncomingLevel[];
  asks?: IncomingLevel[];
};

type QuotesEnvelopeDirect = { quotes?: IncomingQuote[]; type?: string };
type QuotesEnvelopeNested = { data?: { quotes?: IncomingQuote[] }; type?: string };
type DepthEnvelopeDirect  = { depth?: IncomingQuote[];  type?: string };
type DepthEnvelopeNested  = { data?: { depth?: IncomingQuote[] };  type?: string };

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

function extractDepth(payload: unknown): IncomingQuote[] {
  if (isIncomingQuoteArray(payload)) return payload;
  if (!isObject(payload)) return [];
  const direct = (payload as DepthEnvelopeDirect).depth;
  if (isIncomingQuoteArray(direct)) return direct;
  const nested = (payload as DepthEnvelopeNested).data?.depth;
  if (isIncomingQuoteArray(nested)) return nested;
  return [];
}

function toLevelTuple(x: IncomingLevel | undefined): Level | null {
  if (!x) return null;
  if (Array.isArray(x)) {
    const [p, q] = x;
    return typeof p === "number" && typeof q === "number" ? [p, q] : null;
  }
  const p = x.price;
  const q =
    typeof x.qty === "number"
      ? x.qty
      : typeof x.quantity === "number"
      ? x.quantity
      : undefined;
  return typeof p === "number" && typeof q === "number" ? [p, q] : null;
}

const toSym = (s: string | undefined) => (s ? s.trim().toUpperCase() : "");

/** Normalize quotes or depth-like payload to ExtQuote[] */
function normalizeToQuotes(
  payload: unknown,
  dbgTag: string,
  mode: "quotes" | "depth"
): ExtQuote[] {
  const rawArr = mode === "quotes" ? extractQuotes(payload) : extractDepth(payload);
  if (!rawArr.length) return [];

  const now = Date.now();
  const out: ExtQuote[] = [];

  for (const q of rawArr) {
    const sym = toSym(q?.symbol);
    if (!sym) continue;

    const bid = typeof q.bid === "number" && q.bid > 0 ? q.bid : 0;
    const ask = typeof q.ask === "number" && q.ask > 0 ? q.ask : 0;

    const hasL2 =
      (Array.isArray(q.bids) && q.bids.length > 0) ||
      (Array.isArray(q.asks) && q.asks.length > 0);
    const hasL1 = bid > 0 || ask > 0;

    if (mode === "quotes" && !hasL1 && !hasL2) {
      if (import.meta.env.DEV) {
        console.debug(
          `[SSE/drop] ${dbgTag} ${sym} empty frame (bid=0 & ask=0, no L2, ts=${q.ts ?? q.ts_ms ?? "?"})`
        );
      }
      continue;
    }

    const ts =
      typeof q.ts === "number"
        ? q.ts
        : typeof q.ts_ms === "number"
        ? q.ts_ms
        : now;

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

    const bidQty = q.bidQty ?? q.bid_qty;
    const askQty = q.askQty ?? q.ask_qty;

    const bids = Array.isArray(q.bids)
      ? q.bids.map(toLevelTuple).filter((lv): lv is Level => lv !== null)
      : undefined;

    const asks = Array.isArray(q.asks)
      ? q.asks.map(toLevelTuple).filter((lv): lv is Level => lv !== null)
      : undefined;

    out.push({
      symbol: sym,
      bid,
      ask,
      ts,
      mid,
      spread_bps,
      bidQty,
      askQty,
      bids,
      asks,
      ...(typeof q.imbalance === "number" ? { imbalance: q.imbalance } : {}),
    });
  }

  return out;
}

/** Allowed SSE event names */
type SSEEventName = "hello" | "snapshot" | "quotes" | "depth" | "ping" | "message";

/** Map arbitrary string to our known SSEEventName (default to 'message'). */
function asEventName(v?: string): SSEEventName {
  if (v === "hello" || v === "snapshot" || v === "quotes" || v === "depth" || v === "ping" || v === "message") {
    return v;
  }
  return "message";
}

export function useSSEQuotes(symbols: string[]) {
  const applySnapshot = useMarket((s) => s.applySnapshot);
  const applyQuotes   = useMarket((s) => s.applyQuotes);

  const joined = useMemo(() => {
    const set = new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean));
    return Array.from(set).sort().join(",");
  }, [symbols]);

  const intervalEnv = (import.meta.env.VITE_SSE_INTERVAL_MS as string | undefined) ?? "";
  const parsed = Number.parseInt(intervalEnv, 10);
  const intervalMs = Number.isFinite(parsed) ? parsed : 500;

  const handleMessage = useCallback(
    (e: MessageEvent<string> & { event?: string }) => {
      try {
        if (!e.data) return; // some browsers send empty "ping" messages as events
        const payload: unknown = JSON.parse(e.data);

        // prefer explicit SSE event name; fall back to payload.type; finally 'message'
        const typeFromPayload =
          isObject(payload) && typeof (payload as { type?: string }).type === "string"
            ? (payload as { type?: string }).type
            : undefined;

        const eventName: SSEEventName = asEventName(e.event ?? typeFromPayload);

        if (eventName === "hello") {
          // no-op; just confirms the stream is alive
          return;
        } else if (eventName === "snapshot") {
          const quotes = normalizeToQuotes(payload, "snapshot", "quotes");
          if (quotes.length) applySnapshot(quotes);
        } else if (eventName === "quotes" || eventName === "message") {
          const quotes = normalizeToQuotes(payload, "quotes", "quotes");
          if (quotes.length) applyQuotes(quotes);
        } else if (eventName === "depth") {
          const quotes = normalizeToQuotes(payload, "depth", "depth");
          if (quotes.length) applyQuotes(quotes);
        }
      } catch (err) {
        if (import.meta.env.DEV) console.warn("[SSE] malformed frame", err);
      }
    },
    [applySnapshot, applyQuotes]
  );

  useEffect(() => {
    if (!joined) {
      if (import.meta.env.DEV) console.debug("[SSE] skip (no symbols)");
      return;
    }

    if (import.meta.env.DEV) console.debug("[SSE] connect symbols=", joined);
    const dispose = openMarketStream(joined.split(","), intervalMs, handleMessage);

    return () => {
      if (import.meta.env.DEV) console.debug("[SSE] close");
      dispose();
    };
  }, [joined, intervalMs, handleMessage]);
}
