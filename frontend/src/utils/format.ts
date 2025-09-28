// frontend/src/utils/format.ts

// -------------------- Price / Quantity formatters --------------------
export const fmtPrice = (p: number, dp = 5) =>
  (p ?? 0) > 0 ? p.toFixed(dp) : (0).toFixed(dp);

export const fmtQty = (q: number, dp = 2) =>
  (q ?? 0) >= 1000
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: dp }).format(q)
    : (q ?? 0).toFixed(dp);

// -------------------- Symbol helpers --------------------
const QUOTE_SUFFIXES = ["USDT", "USDC", "BTC", "ETH"] as const;

/**
 * Canonical UI/DB form:
 *   - Uppercase
 *   - No separators (BTC_USDT -> BTCUSDT)
 */
export function normalizeSymbol(sym: string | null | undefined): string {
  if (!sym) return "";
  return sym.trim().toUpperCase().replace("_", "");
}

/**
 * Ensure a string is recognized as a known trading pair with quote suffix.
 */
export function isQuoteSymbol(sym: string): boolean {
  const s = normalizeSymbol(sym);
  return QUOTE_SUFFIXES.some((q) => s.endsWith(q));
}

/**
 * Utility: Convert from exchange WS form to UI form.
 *   e.g. "SOL_USDT" -> "SOLUSDT"
 */
export function fromWsSymbol(sym: string): string {
  return normalizeSymbol(sym);
}

/**
 * Utility: Convert to MEXC WS form (BASE_QUOTE with underscore).
 *   e.g. "SOLUSDT" -> "SOL_USDT"
 */
export function toMexcWsSymbol(sym: string): string {
  const s = normalizeSymbol(sym);
  for (const q of QUOTE_SUFFIXES) {
    if (s.endsWith(q)) {
      const base = s.slice(0, -q.length);
      return `${base}_${q}`;
    }
  }
  return s;
}

/**
 * Utility: Convert to Gate WS form (BASE_QUOTE with underscore).
 *   e.g. "BTCUSDT" -> "BTC_USDT"
 */
export function toGateWsSymbol(sym: string): string {
  return toMexcWsSymbol(sym);
}

/**
 * Utility: Convert to Binance WS form (canonical without underscore).
 *   e.g. "BTCUSDT" -> "BTCUSDT"
 */
export function toBinanceWsSymbol(sym: string): string {
  return normalizeSymbol(sym);
}
