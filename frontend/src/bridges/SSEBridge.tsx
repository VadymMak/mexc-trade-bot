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

export default function SSEBridge() {
  const items = useSymbols((s) => s.items);
  const revision = useProvider((s) => s.revision);
  const wsEnabled = useProvider((s) => s.wsEnabled);

  // keep stable references to store writers
  const applySnapshotRef = useRef(useMarket.getState().applySnapshot);
  const applyQuotesRef = useRef(useMarket.getState().applyQuotes);
  useEffect(() => {
    applySnapshotRef.current = useMarket.getState().applySnapshot;
    applyQuotesRef.current = useMarket.getState().applyQuotes;
  });

  // CSV of unique, uppercased symbols
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
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    // If no symbols or WS disabled → close and bail (preserves current quotes; no flicker)
    if (!symbolsCSV || !wsEnabled) {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      reconnectAttempts.current = 0;
      return;
    }

    const connect = () => {
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

      // Reset attempts on successful connection
      const onOpen = () => {
        console.log("[SSE] Connected to market stream");
        reconnectAttempts.current = 0;
      };

      // --- handlers
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

          const patches: Array<Pick<StoreQuote, "symbol" | "bids" | "asks" | "ts_ms">> = depthArr.map((d) => ({
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

      // Optional: support default "message" events carrying a {type} field
      const onMessage = (e: MessageEvent) => {
        try {
          const payload = JSON.parse(e.data) as QuoteFrame | DepthEnvelope;
          if ((payload as QuoteFrame).type === "snapshot") return onSnapshot(e);
          if ((payload as QuoteFrame).type === "quotes") return onQuotes(e);
          if ((payload as DepthEnvelope).type === "depth") return onDepth(e);
        } catch {
          /* ignore parse errors */
        }
      };

      const onError = () => {
        console.warn("[SSE] Connection error, will retry with exponential backoff");
        es.close();

        // Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
        reconnectAttempts.current++;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current - 1), 30000);

        console.log(`[SSE] Reconnecting in ${delay}ms (attempt ${reconnectAttempts.current})`);

        reconnectTimer.current = setTimeout(() => {
          if (symbolsCSV && wsEnabled) {
            connect(); // Recursive reconnect
          }
        }, delay);
      };

      es.addEventListener("open", onOpen);
      es.addEventListener("snapshot", onSnapshot);
      es.addEventListener("quotes", onQuotes);
      es.addEventListener("depth", onDepth);
      es.addEventListener("message", onMessage);
      es.onerror = onError;

      // Cleanup for this connection
      return () => {
        es.removeEventListener("open", onOpen);
        es.removeEventListener("snapshot", onSnapshot);
        es.removeEventListener("quotes", onQuotes);
        es.removeEventListener("depth", onDepth);
        es.removeEventListener("message", onMessage);
        es.onerror = null;
        es.close();
      };
    };

    // Initial connect
    const cleanup = connect();

    // Main cleanup on unmount or dependency change
    return () => {
      cleanup?.();
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      reconnectAttempts.current = 0;
    };
  }, [symbolsCSV, revision, wsEnabled]); // reconnect when watchlist/provider changes or WS toggles

  return null;
}