// frontend/src/utils/format.ts

// -------------------- Number formatters --------------------

/** Generic safe number formatter for UI tables. */
export function formatNumber(
  value: number | null | undefined,
  decimals = 2
): string {
  if (value === null || value === undefined || !isFinite(Number(value))) return "0";
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

/** Compact USD formatter (e.g., $1.2K, $3.4M). */
export function formatUsdCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !isFinite(Number(value))) return "$0";
  return (
    "$" +
    Number(value).toLocaleString(undefined, {
      notation: "compact",
      maximumFractionDigits: 2,
    })
  );
}

/** Percent formatter (e.g., 1.23%). */
export function formatPercent(
  value: number | null | undefined,
  decimals = 2
): string {
  if (value === null || value === undefined || !isFinite(Number(value))) return "0%";
  return `${Number(value).toFixed(decimals)}%`;
}

// -------------------- Price / Quantity formatters --------------------

export const fmtPrice = (p: number | null | undefined, dp = 5): string =>
  Number(p) > 0 ? Number(p).toFixed(dp) : (0).toFixed(dp);

export const fmtQty = (q: number | null | undefined, dp = 2): string => {
  const v = Number(q ?? 0);
  return v >= 1000
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: dp }).format(v)
    : v.toFixed(dp);
};

// -------------------- Symbol helpers --------------------
// keep in sync with frontend filters & backend utils
export const QUOTE_SUFFIXES = ["USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH"] as const;

/**
 * Canonical UI/DB form:
 *   - Uppercase
 *   - No separators (BTC_USDT or BTC/USDT -> BTCUSDT)
 *   - Trim spaces
 */
export function normalizeSymbol(sym: string | null | undefined): string {
  if (!sym) return "";
  // keep only letters/numbers/underscore/slash first, then strip separators
  const trimmed = String(sym).trim();
  return trimmed.toUpperCase().replace(/[_/]/g, "");
}

/**
 * Strict validator for symbols we accept into the app.
 * - UPPERCASE A–Z 0–9 only
 * - length 2..24
 * - must end with one of QUOTE_SUFFIXES (pair)
 */
export function isValidSymbol(sym: unknown): boolean {
  const s = normalizeSymbol(String(sym ?? ""));
  if (!/^[A-Z0-9]{2,24}$/.test(s)) return false;
  return QUOTE_SUFFIXES.some((q) => s.endsWith(q));
}

/** Ensure a string is recognized as a known trading pair with quote suffix. */
export function isQuoteSymbol(sym: string): boolean {
  const s = normalizeSymbol(sym);
  return QUOTE_SUFFIXES.some((q) => s.endsWith(q));
}

/** Exchange WS → UI (e.g., "SOL_USDT" -> "SOLUSDT"). */
export function fromWsSymbol(sym: string): string {
  return normalizeSymbol(sym);
}

/** UI → MEXC WS (e.g., "SOLUSDT" -> "SOL_USDT"). */
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

/** UI → Gate WS (same pattern as MEXC for spot). */
export function toGateWsSymbol(sym: string): string {
  return toMexcWsSymbol(sym);
}

/** UI → Binance WS (already canonical, no underscore). */
export function toBinanceWsSymbol(sym: string): string {
  return normalizeSymbol(sym);
}

/**
 * Parse a free-form user input into a clean, unique list of symbols.
 * Splits on non-alphanumerics, normalizes, validates, de-dupes.
 */
export function parseSymbolsInput(input: string): {
  good: string[];
  bad: string[];
} {
  const parts = String(input ?? "")
    .split(/[^A-Za-z0-9]+/g)
    .map(normalizeSymbol)
    .filter(Boolean);

  const seen = new Set<string>();
  const good: string[] = [];
  const badSet = new Set<string>();

  for (const p of parts) {
    if (isValidSymbol(p)) {
      if (!seen.has(p)) {
        seen.add(p);
        good.push(p);
      }
    } else if (p) {
      badSet.add(p);
    }
  }
  return { good, bad: Array.from(badSet) };
}
