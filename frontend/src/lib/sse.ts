// src/lib/sse.ts

// Keep a single active EventSource per URL to avoid duplicate streams
let _activeES: EventSource | null = null;
let _activeURL = "";

type MsgHandler = (ev: MessageEvent<string>) => void;

/**
 * Open (or reuse) a single SSE connection.
 * - If called with a DIFFERENT url, closes the previous stream first.
 * - If called with the SAME url, reuses the stream and only (re)attaches listeners.
 * Returns a disposer that detaches listeners and, if this instance owns the stream, closes it.
 */
export function openSSE(
  url: string,
  onEvent: MsgHandler,
  eventNames: string[] = [],
): () => void {
  // credentials flag (optional)
  const withCreds =
    typeof import.meta !== "undefined" &&
    (import.meta.env?.VITE_SSE_WITH_CREDENTIALS === "1" ||
      import.meta.env?.VITE_SSE_WITH_CREDENTIALS === "true");

  // If there is an active stream but for a different URL — close it
  if (_activeES && _activeURL && _activeURL !== url) {
    try { _activeES.close(); } catch {console.log("Some error")}
    _activeES = null;
    _activeURL = "";
  }

  // Reuse if same URL is already active
  let es = _activeES;
  if (!es) {
    es = new EventSource(url, withCreds ? { withCredentials: true } : undefined);
    _activeES = es;
    _activeURL = url;

    if (import.meta.env?.DEV) {
      es.addEventListener("open", () => console.debug("[SSE] open →", url), { once: true });
      es.addEventListener("error", (e) => console.debug("[SSE] error", e), false);
    }
  }

  // Attach listeners for this caller (track them to remove later)
  const namedHandlers: Record<string, (ev: Event) => void> = {};
  const safeOnEvent = (ev: Event) => onEvent(ev as MessageEvent<string>);

  // Default 'message' fallback — always attach
  es.addEventListener("message", safeOnEvent, false);

  // De-dupe event names and avoid double 'message'
  const uniq = Array.from(new Set(eventNames.filter((n) => n && n !== "message")));
  for (const name of uniq) {
    const handler = (ev: Event) => onEvent(ev as MessageEvent<string>);
    namedHandlers[name] = handler;
    es.addEventListener(name, handler, false);
  }

  // Disposer removes THIS caller’s handlers.
  // If this ES is still the active one, we also close it.
  const disposer = () => {
    try {
      es?.removeEventListener("message", safeOnEvent, false);
      for (const [name, handler] of Object.entries(namedHandlers)) {
        es?.removeEventListener(name, handler, false);
      }
    } catch {console.log("Some error")}

    if (_activeES === es) {
      try { _activeES.close(); } catch {console.log("Some error")}
      _activeES = null;
      _activeURL = "";
      if (import.meta.env?.DEV) console.debug("[SSE] closed →", url);
    }
  };

  return disposer;
}
