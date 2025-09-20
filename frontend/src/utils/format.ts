// utils/format.ts
export const fmtPrice = (p: number, dp = 5) =>
  (p ?? 0) > 0 ? p.toFixed(dp) : (0).toFixed(dp);

export const fmtQty = (q: number, dp = 2) =>
  (q ?? 0) >= 1000
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: dp }).format(q)
    : (q ?? 0).toFixed(dp);
