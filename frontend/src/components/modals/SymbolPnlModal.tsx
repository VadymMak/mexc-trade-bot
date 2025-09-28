// src/components/modals/SymbolPnlModal.tsx
import { useEffect, useMemo, useState } from "react";
import cx from "classnames";
import { usePnlStore } from "@/store/pnl";

export type SymbolPnlModalProps = {
  open: boolean;
  symbol: string | null;  // UPPERCASE symbol or null when closed
  onClose: () => void;
};

function fmtUsd(n: unknown): string {
  const v = typeof n === "number" ? n : NaN;
  if (!Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  const abs = Math.abs(v);
  return `${sign}$${abs.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
function fmtNum(n: unknown, dp = 6): string {
  const v = typeof n === "number" ? n : NaN;
  if (!Number.isFinite(v)) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: dp,
  });
}
function fmtISO(ts: unknown): string {
  if (typeof ts === "string" && ts) return ts;
  if (typeof ts === "number" && Number.isFinite(ts)) {
    try { return new Date(ts).toISOString(); } catch { /* noop */ }
  }
  return "—";
}

// safe dynamic readers
const readNum = (
  obj: Record<string, unknown> | null | undefined,
  keys: readonly string[]
): number | null => {
  if (!obj) return null;
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
    // some backends send numeric strings; be lenient but safe
    if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) {
      const n = Number(v);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
};
const readStr = (
  obj: Record<string, unknown> | null | undefined,
  keys: readonly string[]
): string | undefined => {
  if (!obj) return undefined;
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "string" && v) return v;
  }
  return undefined;
};

export default function SymbolPnlModal({
  open,
  symbol,
  onClose,
}: SymbolPnlModalProps) {
  // mount/unmount animation
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    if (open) {
      setMounted(true);
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
      const t = setTimeout(() => setMounted(false), 150);
      return () => clearTimeout(t);
    }
  }, [open]);

  // report controls & fetcher from shared store
  const params = usePnlStore((s) => s.params);
  const fetchSymbolDetail = usePnlStore((s) => s.fetchSymbolDetail);

  // local copy of fetched data
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summaryUSD, setSummaryUSD] = useState<number | null>(null);
  const [components, setComponents] =
    useState<Record<string, unknown> | null>(null);
  const [events, setEvents] =
    useState<ReadonlyArray<Record<string, unknown>> | null>(null);

  // Load data when opened / symbol or report params change
  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!open || !symbol) return;
      setLoading(true);
      setError(null);
      const entry = await fetchSymbolDetail(symbol, { force: true });
      if (cancelled) return;
      setLoading(entry.loading);
      setError(entry.error ?? null);
      setSummaryUSD(
        typeof entry.data?.total_usd === "number" ? entry.data.total_usd : null
      );
      setComponents(entry.data?.components ?? null);

      // normalize & sort events by time when possible
      const raw = Array.isArray(entry.data?.last_events)
        ? (entry.data!.last_events as ReadonlyArray<Record<string, unknown>>)
        : null;
      if (raw && raw.length) {
        const withSortKey = raw.map((e) => {
          const tStr = readStr(e, ["time", "ts", "timestamp", "created_at", "event_time"]);
          const tNum = readNum(e, ["time", "ts"]); // if numeric
          const sk = typeof tNum === "number"
            ? tNum
            : (tStr ? Date.parse(tStr) : NaN);
        return { e, sk };
        });
        withSortKey.sort((a, b) => {
          const an = Number.isFinite(a.sk) ? a.sk : 0;
          const bn = Number.isFinite(b.sk) ? b.sk : 0;
          return bn - an; // newest first
        });
        setEvents(withSortKey.map((x) => x.e));
      } else {
        setEvents(null);
      }

      setLoading(false);
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [
    open,
    symbol,
    params.period,
    params.tz,
    params.exchange,
    params.accountId,
    params.fromISO,
    params.toISO,
    fetchSymbolDetail,
  ]);

  // derive KPIs
  const realized = useMemo(
    () => readNum(components, ["realized", "realized_usd", "rpnl", "realizedPnl"]),
    [components]
  );
  const unrealized = useMemo(
    () => readNum(components, ["unrealized", "unrealized_usd", "upnl", "unrealizedPnl"]),
    [components]
  );
  const fees = useMemo(
    () => readNum(components, ["fees", "fees_usd", "commission", "commission_usd"]),
    [components]
  );
  const netTotal = useMemo(() => {
    if (typeof summaryUSD === "number") return summaryUSD;
    const r = typeof realized === "number" ? realized : 0;
    const u = typeof unrealized === "number" ? unrealized : 0;
    const f = typeof fees === "number" ? fees : 0;
    const sum = r + u - Math.abs(f);
    return Number.isFinite(sum) ? sum : null;
  }, [summaryUSD, realized, unrealized, fees]);

  if (!open && !mounted) return null;

  const periodLabel =
    params.period === "today"
      ? "Today"
      : params.period === "wtd"
      ? "Week"
      : params.period === "mtd"
      ? "Month"
      : "Custom";

  return (
    <div
      className={cx(
        "fixed inset-0 z-[100] flex items-center justify-center",
        "bg-black/50 backdrop-blur-sm",
        open ? "opacity-100" : "opacity-0",
        "transition-opacity duration-150"
      )}
      onClick={onClose}
    >
      <div
        className={cx(
          "w-[880px] max-w-[95vw]",
          "rounded-2xl border border-neutral-800 bg-neutral-900 shadow-2xl",
          "transition-transform duration-150",
          open ? "scale-100" : "scale-95"
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
          <div className="flex items-baseline gap-3">
            <h3 className="text-lg font-semibold text-neutral-100">
              PnL Details {symbol ? `— ${symbol}` : ""}
            </h3>
            <div className="text-xs text-neutral-400">
              Period: {periodLabel}
              {params.period === "custom" && params.fromISO && params.toISO ? (
                <> · {params.fromISO} → {params.toISO}</>
              ) : null}
              {" · "}TZ: {params.tz}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled
              title="Export CSV (soon)"
              className="px-3 py-1.5 text-sm rounded-lg border border-neutral-700 text-neutral-300 disabled:opacity-60"
            >
              Export CSV
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-sm rounded-lg bg-neutral-800 hover:bg-neutral-700 text-neutral-200"
            >
              Close
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="px-4 py-4">
          {loading && (
            <div className="mb-3 text-sm text-neutral-400">Loading…</div>
          )}
          {error && (
            <div className="mb-3 rounded-md border border-rose-800 bg-rose-900/30 px-3 py-2 text-sm text-rose-200">
              {error}
            </div>
          )}

          {/* KPIs */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <div className="rounded-xl bg-neutral-800/70 p-3 border border-neutral-800">
              <div className="text-xs text-neutral-400">Realized</div>
              <div
                className={cx(
                  "text-base font-semibold",
                  (realized ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                )}
              >
                {fmtUsd(realized)}
              </div>
            </div>
            <div className="rounded-xl bg-neutral-800/70 p-3 border border-neutral-800">
              <div className="text-xs text-neutral-400">Unrealized</div>
              <div
                className={cx(
                  "text-base font-semibold",
                  (unrealized ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                )}
              >
                {fmtUsd(unrealized)}
              </div>
            </div>
            <div className="rounded-xl bg-neutral-800/70 p-3 border border-neutral-800">
              <div className="text-xs text-neutral-400">Fees</div>
              <div className="text-base font-semibold text-neutral-200">
                {fmtUsd(fees)}
              </div>
            </div>
            <div className="rounded-xl bg-neutral-800/70 p-3 border border-neutral-800">
              <div className="text-xs text-neutral-400">Net Total</div>
              <div
                className={cx(
                  "text-base font-semibold",
                  (netTotal ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                )}
              >
                {fmtUsd(netTotal)}
              </div>
            </div>
          </div>

          {/* Recent Events */}
          <div className="rounded-xl border border-neutral-800 overflow-hidden">
            <div className="px-3 py-2 text-xs text-neutral-400 border-b border-neutral-800 bg-neutral-900/80">
              Recent Events
            </div>
            <div className="max-h-[360px] overflow-auto">
              <table className="min-w-full text-sm text-neutral-200">
                <thead className="sticky top-0 bg-neutral-900/90 text-neutral-400">
                  <tr>
                    <th className="px-3 py-2 text-left">Time</th>
                    <th className="px-3 py-2 text-left">Type</th>
                    <th className="px-3 py-2 text-left">Side</th>
                    <th className="px-3 py-2 text-right">Qty</th>
                    <th className="px-3 py-2 text-right">Price</th>
                    <th className="px-3 py-2 text-right">Fee</th>
                    <th className="px-3 py-2 text-right">PnL Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {!events || events.length === 0 ? (
                    <tr>
                      <td className="px-3 py-3 text-neutral-500" colSpan={7}>
                        {loading ? "Loading…" : "No recent events"}
                      </td>
                    </tr>
                  ) : (
                    events.map((e, idx) => {
                      // Expanded aliases to match various backends
                      const time = readStr(e, ["time", "ts", "timestamp", "created_at", "event_time"]);
                      const type = readStr(e, ["type", "event", "kind", "category", "name"]);
                      const side = readStr(e, ["side", "direction", "action", "taker_side", "maker_side"]);
                      const qty  = readNum(e, ["qty", "quantity", "size", "amount", "filled_qty", "base_qty", "exec_qty"]);
                      const price = readNum(e, ["price", "avg_price", "fill_price", "mark", "exec_price"]);
                      const fee   = readNum(e, ["fee", "fees", "fee_usd", "commission", "commission_usd"]);
                      const pnl   = readNum(e, [
                        "pnl", "pnl_delta", "delta", "rpnl_delta", "realized_delta",
                        "realized", "realized_usd", "upnl_delta"
                      ]);

                      return (
                        <tr key={idx} className="border-t border-neutral-800/60">
                          <td className="px-3 py-2">{fmtISO(time)}</td>
                          <td className="px-3 py-2">{type ?? "—"}</td>
                          <td className="px-3 py-2">{side ?? "—"}</td>
                          <td className="px-3 py-2 text-right">{fmtNum(qty)}</td>
                          <td className="px-3 py-2 text-right">{fmtNum(price)}</td>
                          <td className="px-3 py-2 text-right">{fmtUsd(fee)}</td>
                          <td
                            className={cx(
                              "px-3 py-2 text-right",
                              typeof pnl === "number"
                                ? pnl >= 0
                                  ? "text-emerald-400"
                                  : "text-red-400"
                                : "text-neutral-200"
                            )}
                          >
                            {fmtUsd(pnl)}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
