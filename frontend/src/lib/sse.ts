// src/lib/sse.ts

/** Safely read a Vite env value as string (or undefined). */
function envStr(name: string): string | undefined {
  const im: unknown = typeof import.meta !== "undefined" ? import.meta : undefined;
  if (!im || typeof im !== "object") return undefined;
  const env = (im as { env?: unknown }).env;
  if (!env || typeof env !== "object") return undefined;
  const v = (env as Record<string, unknown>)[name];
  return typeof v === "string" ? v : undefined;
}

/** True when Vite DEV is strictly boolean true. */
function isDevEnv(): boolean {
  const im: unknown = typeof import.meta !== "undefined" ? import.meta : undefined;
  if (!im || typeof im !== "object") return false;
  const env = (im as { env?: unknown }).env;
  if (!env || typeof env !== "object") return false;
  return (env as Record<string, unknown>).DEV === true;
}

/** Build an SSE URL with query params. Uses VITE_API_BASE_URL if set, else same-origin. */
export function buildSSEUrl(path: string, params?: Record<string, unknown>): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  // IMPORTANT: align with http.ts env name
  const base = envStr("VITE_API_BASE_URL") ?? "";

  const url = base
    ? new URL(p, base.endsWith("/") ? base : `${base}/`)
    : new URL(p, window.location.origin);

  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null) continue;
      if (Array.isArray(v)) {
        for (const it of v) {
          if (it === undefined || it === null) continue;
          const s = String(it).trim();
          if (s) url.searchParams.append(k, s);
        }
      } else {
        const s = String(v).trim();
        if (s) url.searchParams.set(k, s);
      }
    }
  }

  return url.toString();
}

// ──────────────────────────────────────────────────────────────────────────────
// Keep a single active EventSource per URL to avoid duplicate streams
let _activeES: EventSource | null = null;
let _activeURL = "";

type MsgHandler = (ev: MessageEvent<string> & { event?: string }) => void;

/**
 * Open (or reuse) a single SSE connection.
 * - If called with a DIFFERENT url, closes the previous stream first.
 * - If called with the SAME url, reuses the stream and only (re)attaches listeners.
 * Returns a disposer that detaches listeners and, if this instance owns the stream, closes it.
 */
export function openSSE(
  url: string,
  onEvent: MsgHandler,
  eventNames: string[] = []
): () => void {
  const withCreds = (() => {
    const v = envStr("VITE_SSE_WITH_CREDENTIALS");
    return v === "1" || v === "true";
  })();

  // Close previous if URL changed
  if (_activeES && _activeURL && _activeURL !== url) {
    try {
      _activeES.close();
      if (isDevEnv()) console.debug("[SSE] closed previous →", _activeURL);
    } catch (err) {
      if (isDevEnv()) console.warn("[SSE] close failed", err);
    }
    _activeES = null;
    _activeURL = "";
  }

  // Reuse or create
  let es = _activeES;
  if (!es) {
    const opts: EventSourceInit | undefined = withCreds ? { withCredentials: true } : undefined;
    es = new EventSource(url, opts);
    _activeES = es;
    _activeURL = url;

    if (isDevEnv()) {
      es.addEventListener("open", () => console.debug("[SSE] open →", url), { once: true });
      es.addEventListener("error", (e) => console.debug("[SSE] error", e), false);
    }
  } else if (isDevEnv()) {
    const state = es.readyState === 0 ? "CONNECTING" : es.readyState === 1 ? "OPEN" : "CLOSED";
    console.debug("[SSE] reuse", { url, state });
  }

  // Attach handlers
  const namedHandlers: Record<string, (ev: Event) => void> = {};
  const enrich = (ev: Event, name: string): MessageEvent<string> & { event?: string } => {
    const msg = ev as MessageEvent<string>;
    return { ...msg, event: name };
    // Note: msg.data is the JSON string; callers should JSON.parse(msg.data) safely.
  };

  const messageHandler = (ev: Event) => onEvent(enrich(ev, "message"));
  es.addEventListener("message", messageHandler, false);

  // Dedup named events (don’t double-add "message")
  const uniq = Array.from(new Set(eventNames.filter((n) => n && n !== "message")));
  for (const name of uniq) {
    const handler = (ev: Event) => onEvent(enrich(ev, name));
    namedHandlers[name] = handler;
    es.addEventListener(name, handler, false);
  }

  // Disposer for this caller
  const disposer = () => {
    try {
      es?.removeEventListener("message", messageHandler, false);
      for (const [name, handler] of Object.entries(namedHandlers)) {
        es?.removeEventListener(name, handler, false);
      }
    } catch (err) {
      if (isDevEnv()) console.warn("[SSE] removeEventListener failed", err);
    }

    if (_activeES === es) {
      try {
        _activeES.close();
        if (isDevEnv()) console.debug("[SSE] closed →", url);
      } catch (err) {
        if (isDevEnv()) console.warn("[SSE] close failed", err);
      }
      _activeES = null;
      _activeURL = "";
    }
  };

  return disposer;
}

/** Convenience: build and open the market stream with symbols/interval. */
export function openMarketStream(
  symbols: string[],
  intervalMs = 500,
  onEvent: MsgHandler
): () => void {
  const syms = symbols
    .map((s) => String(s || "").trim().toUpperCase())
    .filter(Boolean)
    .join(",");

  const url = buildSSEUrl("/api/market/stream", {
    symbols: syms,
    interval_ms: String(intervalMs),
  });

  // Listen for our known event names from the backend
  return openSSE(url, onEvent, ["hello", "snapshot", "quotes", "depth", "ping", "pnl_tick"]);
}
