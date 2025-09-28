// src/bridges/SSEBridge.tsx
import { useEffect, useMemo, useRef } from "react";
import { useSymbols } from "@/store/symbols";
import { useMarket, type StoreQuote } from "@/store/market";
import { useProvider } from "@/store/provider";
import { buildSSEUrl } from "@/lib/sse";

type QuoteFrame = {
  type?: "snapshot" | "quotes";
  quotes?: StoreQuote[];
};

type DepthFrame = {
  symbol?: string;
  bids?: [number, number][];
  asks?: [number, number][];
  ts_ms?: number;
};

type DepthEnvelope = {
  type?: "depth";
  depth?: DepthFrame[];
};

/** Wire up SSE stream to the market store and auto-reconnect on changes. */
export default function SSEBridge() {
  const items = useSymbols((s) => s.items);
  const revision = useProvider((s) => s.revision);

  // Stable refs to store writers so the effect doesn’t re-run on their identity changes
  const applySnapshotRef = useRef(useMarket.getState().applySnapshot);
  const applyQuotesRef = useRef(useMarket.getState().applyQuotes);
  // keep refs fresh in case store hot-swaps them (unlikely, but cheap)
  useEffect(() => {
    applySnapshotRef.current = useMarket.getState().applySnapshot;
    applyQuotesRef.current = useMarket.getState().applyQuotes;
  });

  // Stable CSV of unique, uppercased symbols
  const symbolsCSV = useMemo(() => {
    const seen = new Set<string>();
    const list: string[] = [];
    for (const it of items) {
      const sym = (it?.symbol ?? "").trim().toUpperCase();
      if (sym && !seen.has(sym)) {
        seen.add(sym);
        list.push(sym);
      }
    }
    return list.join(",");
  }, [items]);

  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // If no symbols → close and bail (keep existing quotes; avoid flicker)
    if (!symbolsCSV) {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      return;
    }

    // Close previous before opening new
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    const url = buildSSEUrl("/api/market/stream", {
      symbols: symbolsCSV,
      interval_ms: "500",
    });

    const es = new EventSource(url);
    esRef.current = es;

    // --- handlers (use refs to stable store writers)
    const onSnapshot = (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data) as QuoteFrame;
        const quotes = Array.isArray(payload?.quotes) ? payload.quotes : [];
        if (quotes.length) applySnapshotRef.current(quotes);
      } catch {
        /* ignore parse errors */
      }
    };

    const onQuotes = (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data) as QuoteFrame;
        const quotes = Array.isArray(payload?.quotes) ? payload.quotes : [];
        if (quotes.length) applyQuotesRef.current(quotes);
      } catch {
        /* ignore parse errors */
      }
    };

    // Depth frames: { type:"depth", depth:[{symbol,bids,asks,ts_ms}] }
    const onDepth = (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data) as DepthEnvelope;
        const depthArr = Array.isArray(payload?.depth) ? payload.depth : [];
        if (!depthArr.length) return;

        // Convert depth items into StoreQuote-like patches
        const patches: Array<
          Pick<StoreQuote, "symbol" | "bids" | "asks" | "ts_ms">
        > = depthArr.map((d) => ({
          symbol: String(d.symbol ?? "").toUpperCase(),
          bids: Array.isArray(d.bids) ? d.bids : undefined,
          asks: Array.isArray(d.asks) ? d.asks : undefined,
          ts_ms: typeof d.ts_ms === "number" ? d.ts_ms : Date.now(),
        }));

        if (patches.length) applyQuotesRef.current(patches as StoreQuote[]);
      } catch {
        /* ignore parse errors */
      }
    };

    const onError = () => {
      // Let browser auto-reconnect; avoid setState loops here.
    };

    es.addEventListener("snapshot", onSnapshot);
    es.addEventListener("quotes", onQuotes);
    es.addEventListener("depth", onDepth);
    es.onerror = onError;

    return () => {
      es.removeEventListener("snapshot", onSnapshot);
      es.removeEventListener("quotes", onQuotes);
      es.removeEventListener("depth", onDepth);
      es.onerror = null;
      es.close();
      esRef.current = null;
    };
  }, [symbolsCSV, revision]); // ← only reconnect when watchlist or provider changes

  return null;
}
