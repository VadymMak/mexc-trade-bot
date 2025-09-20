// src/lib/http.ts
import axios, { AxiosError, AxiosInstance } from "axios";
import { useToastStore } from "@/store/toast";

/**
 * In DEV we default to a relative baseURL ("") so Vite's proxy can forward /api to 8000.
 * In PROD (or if you really want to bypass the proxy), set VITE_API_BASE_URL.
 */
function resolveBaseURL(): string {
  const envBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();
  if (envBase) return envBase.replace(/\/+$/, "");
  return ""; // relative → go through Vite proxy (or same-origin in prod)
}

/** Read timeout from env, fallback 30s. */
function resolveTimeoutMs(): number {
  const raw = (import.meta.env.VITE_HTTP_TIMEOUT_MS as string | undefined)?.trim();
  const n = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 30_000;
}

const http: AxiosInstance = axios.create({
  baseURL: resolveBaseURL(),
  timeout: resolveTimeoutMs(),
  withCredentials: false, // flip to true only if you start using cookies
  headers: {
    Accept: "application/json",
  },
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
    const isTimeout = error.code === "ECONNABORTED" || /timeout/i.test(String(error.message));
    const status = error.response?.status;
    const data = error.response?.data;

    let detail: string | undefined;
    if (data && typeof data === "object") {
      const d = (data as Record<string, unknown>).detail;
      const m = (data as Record<string, unknown>).message;
      if (typeof d === "string") detail = d;
      else if (typeof m === "string") detail = m;
    }

    const timeoutMs = typeof http.defaults.timeout === "number" ? http.defaults.timeout : resolveTimeoutMs();
    const msg =
      detail ??
      (isTimeout ? `timeout of ${timeoutMs}ms exceeded` : error.message ?? "Request failed");

    try {
      useToastStore.getState().add({
        kind: "error",
        title: "HTTP Error",
        message: `[${status ?? (isTimeout ? "TIMEOUT" : "ERR")}] ${msg}`,
        timeoutMs: 5500,
      });
    } catch {
      /* ignore toast store errors */
    }

    return Promise.reject(new Error(`[${status ?? (isTimeout ? "TIMEOUT" : "ERR")}] ${msg}`));
  }
);

export default http;
