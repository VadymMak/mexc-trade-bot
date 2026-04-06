// src/lib/http.ts
import axios, { type AxiosError, type AxiosInstance } from "axios";
import { useToastStore } from "@/store/toast";

/**
 * In Next.js all /api/* calls are proxied through src/app/api/proxy/[...path]/route.ts
 * which forwards them to the Railway backend (BACKEND_URL env var on server).
 * Client always uses relative /api/proxy/* — no CORS issues.
 */
function resolveBaseURL(): string {
  // In Next.js we always use the proxy at /api/proxy
  return "/api/proxy";
}

/** Read timeout from env, fallback 90s. */
function resolveTimeoutMs(): number {
  const raw = (process.env.NEXT_PUBLIC_HTTP_TIMEOUT_MS ?? "").trim();
  const n = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 90_000;
}

const http: AxiosInstance = axios.create({
  baseURL: resolveBaseURL(),
  timeout: resolveTimeoutMs(),
  withCredentials: false, // flip to true only if you start using cookies
  headers: {
    Accept: "application/json",
    "X-Requested-With": "XMLHttpRequest",
  },
  // pass through only 2xx by default
  validateStatus: (status) => status >= 200 && status < 300,
});

// Strip Content-Type on GET to avoid preflights on some setups
http.interceptors.request.use((cfg) => {
  if (cfg.method?.toUpperCase() === "GET" && cfg.headers) {
    // AxiosHeaders is indexable but TS isn't happy without this cast
    const h = cfg.headers as Record<string, unknown>;
    delete h["Content-Type"];
  }
  return cfg;
});

// ---- Response error normalization + global toast ----
http.interceptors.response.use(
  (res) => res,
  (error: AxiosError) => {
    // Axios sets code='ECONNABORTED' on timeouts
    const isTimeout =
      error.code === "ECONNABORTED" || /timeout/i.test(String(error.message));

    const status = error.response?.status;
    const data = error.response?.data;

    // prefer detail/message fields; gracefully handle arrays/objects
    let detail: string | undefined;
    if (data && typeof data === "object") {
      const d = data as Record<string, unknown>;
      if (typeof d.detail === "string" && d.detail.trim()) detail = d.detail;
      else if (typeof d.message === "string" && d.message.trim()) detail = d.message;
      else if (Array.isArray(d.detail)) {
        const parts = d.detail
          .map((it) =>
            typeof it === "string"
              ? it
              : typeof it === "object" && it !== null
              ? String((it as Record<string, unknown>).message ?? (it as Record<string, unknown>).msg ?? "")
              : ""
          )
          .filter(Boolean);
        if (parts.length) detail = parts.join("; ");
      }
    }

    const timeoutMs =
      typeof http.defaults.timeout === "number"
        ? http.defaults.timeout
        : resolveTimeoutMs();

    const msg =
      detail ??
      (isTimeout ? `timeout of ${timeoutMs}ms exceeded` : error.message ?? "Request failed");

    // fire-and-forget toast (guard if store not ready)
    try {
      useToastStore.getState().add({
        kind: "error",
        title: "HTTP Error",
        message: `[${status ?? (isTimeout ? "TIMEOUT" : "ERR")}] ${msg}`,
        timeoutMs: 5500,
      });
    } catch {
      /* ignore toast store access issues */
    }

    // normalize rejection so callers get a simple Error with user-readable text
    return Promise.reject(
      new Error(`[${status ?? (isTimeout ? "TIMEOUT" : "ERR")}] ${msg}`)
    );
  }
);

export default http;
