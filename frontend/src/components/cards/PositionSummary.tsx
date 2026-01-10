// src/components/cards/PositionSummary.tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { usePositionsStore, type Position } from "@/store/positions";
import { useMarket } from "@/store/market";

export type MarkGetter = (symbol: string) => number | undefined;

export type PositionSummaryProps = {
  compact?: boolean;
  getMarkPrice?: MarkGetter;
};

type Period = "today" | "wtd" | "mtd" | "custom";

type PnlSummary = {
  period: Period | string;
  total_usd: number;
};

function fmtUsd(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function browserTZ(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export default function PositionSummary({
  compact = false,
  getMarkPrice,
}: PositionSummaryProps) {
  // ----- Market: quotes + lightweight tick -----
  const quoteOf = useMarket((s) => s.quoteOf);
  const quotesTick = useMarket((s) => s.quotesTick);

  // Mark: mid(bid,ask) → bid/ask
  const getMarkFromStore = useCallback<MarkGetter>(
    (symbol: string) => {
      const q = quoteOf(symbol);
      const bid = typeof q?.bid === "number" && q.bid > 0 ? q.bid : undefined;
      const ask = typeof q?.ask === "number" && q.ask > 0 ? q.ask : undefined;
      if (typeof bid === "number" && typeof ask === "number") {
        return (bid + ask) / 2;
      }
      return (bid ?? ask) as number | undefined;
    },
    [quoteOf]
  );

  const markGetter: MarkGetter = getMarkPrice ?? getMarkFromStore;

  // ----- Store access -----
  const loading = usePositionsStore((s) => s.loading);
  const error = usePositionsStore((s) => s.error);
  const positionsMap = usePositionsStore((s) => s.positionsBySymbol);
  const totalUPnL = usePositionsStore((s) => s.totalUPnL);

  // ----- Period / TZ controls (local UI state) -----
  const [period, setPeriod] = useState<Period>("today");
  const [tz, setTz] = useState<string>(browserTZ());

  // Custom date range (UTC ISO)
  const [fromISO, setFromISO] = useState<string>("");
  const [toISO, setToISO] = useState<string>("");

  // rPnL summary fetched from backend (always visible)
  const [pnl, setPnl] = useState<number>(0);
  const [pnlLoading, setPnlLoading] = useState<boolean>(false);
  const [pnlError, setPnlError] = useState<string | null>(null);

  const buildSummaryUrl = useCallback(() => {
    const params = new URLSearchParams();
    params.set("period", period);
    if (tz) params.set("tz", tz);
    if (period === "custom" && fromISO && toISO) {
      params.set("from", fromISO);
      params.set("to", toISO);
    }
    return `/api/pnl/summary?${params.toString()}`;
  }, [period, tz, fromISO, toISO]);

  const fetchSummary = useCallback(async () => {
    try {
      setPnlLoading(true);
      setPnlError(null);
      const res = await fetch(buildSummaryUrl(), {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`GET /api/pnl/summary failed: ${res.status} ${text}`);
      }
      const data: PnlSummary = await res.json();
      const v = typeof data?.total_usd === "number" ? data.total_usd : 0;
      setPnl(v);
    } catch (e) {
      setPnlError(e instanceof Error ? e.message : "Failed to load PnL summary");
    } finally {
      setPnlLoading(false);
    }
  }, [buildSummaryUrl]);

  // Poll rPnL summary every 5s (period/TZ aware)
  useEffect(() => {
    void fetchSummary();
    const id = window.setInterval(() => void fetchSummary(), 15000);
    return () => clearInterval(id);
  }, [fetchSummary]);

  // ----- Active positions (qty ≠ 0) -----
  const activePositions: Position[] = useMemo(
    () => Object.values(positionsMap).filter((p) => Math.abs(p.qty) > 0),
    [positionsMap]
  );

  const count = activePositions.length;

  // Exposure / uPnL from live (only active)
  const exposure = useMemo(() => {
    void quotesTick;
    if (activePositions.length === 0) return 0;
    return activePositions.reduce((t, p) => {
      const mark = markGetter(p.symbol);
      return t + (mark && Number.isFinite(p.qty) ? Math.abs(p.qty) * mark : 0);
    }, 0);
  }, [activePositions, markGetter, quotesTick]);

  const uPnL = useMemo(() => {
    void quotesTick;
    if (activePositions.length === 0) return 0;
    return totalUPnL(markGetter);
  }, [activePositions, totalUPnL, markGetter, quotesTick]);

  // rPnL — always shown from backend summary for chosen period/TZ
  const rPnL = pnl;

  // ----- UI bits -----
  const wrapPad = compact ? "p-3" : "p-4";
  const titleCls = compact ? "text-sm font-semibold" : "text-base font-semibold";
  const gridCls = compact ? "grid grid-cols-3 gap-2" : "grid grid-cols-3 gap-3";
  const valueCls = (pos: boolean) =>
    [
      compact ? "text-sm" : "text-base",
      "font-semibold",
      pos ? "text-emerald-400" : "text-red-400",
    ].join(" ");

  // Fixed widths to avoid layout shift
  const PERIOD_W = "w-[92px]";        // select width for Period
  const TZ_W = "w-[190px]";           // select width for TZ
  const RANGE_W = "w-[200px]";        // inputs in custom range
  const HINT_W = "min-w-[130px]";     // right-side hint container

  const selectBase =
    "h-8 rounded-md border border-neutral-700 bg-neutral-900/70 px-2 text-xs text-neutral-200 outline-none";

  const periodLabel = (p: Period) =>
    p === "today" ? "Today" : p === "wtd" ? "Week" : p === "mtd" ? "Month" : "Custom";

  const commonTZs = useMemo(() => {
    const z = new Set<string>([
      browserTZ(),
      "UTC",
      "Europe/London",
      "Europe/Berlin",
      "Europe/Istanbul",
      "America/New_York",
      "America/Chicago",
      "America/Los_Angeles",
      "Asia/Dubai",
      "Asia/Singapore",
      "Asia/Tokyo",
    ]);
    return Array.from(z);
  }, []);

  return (
    <div
      className={[
        "w-full rounded-2xl border border-neutral-800 bg-neutral-900/60 shadow-lg",
        wrapPad,
      ].join(" ")}
    >
      {/* Header Row */}
      <div className={compact ? "mb-2 flex items-center justify-between" : "mb-3 flex items-center justify-between"}>
        <h3 className={`${titleCls} text-neutral-100`}>
          Position Summary {count > 0 ? `(${count})` : ""}
        </h3>
        <div className="text-xs text-neutral-500">
          {error ? <span className="text-red-400">{error}</span> : loading ? "Updating…" : "Live"}
        </div>
      </div>

      {/* Controls Row (fixed control widths to prevent jumping) */}
      <div className={compact ? "mb-2 flex flex-wrap items-center gap-2" : "mb-3 flex flex-wrap items-center gap-3"}>
        <label className="flex items-center gap-2 text-xs text-neutral-400">
          <span>Period</span>
          <select
            className={`${selectBase} ${PERIOD_W}`}
            value={period}
            onChange={(e) => setPeriod(e.target.value as Period)}
          >
            <option value="today">Today</option>
            <option value="wtd">Week</option>
            <option value="mtd">Month</option>
            <option value="custom">Custom</option>
          </select>
        </label>

        <label className="flex items-center gap-2 text-xs text-neutral-400">
          <span>TZ</span>
          <select
            className={`${selectBase} ${TZ_W}`}
            value={tz}
            onChange={(e) => setTz(e.target.value)}
          >
            {commonTZs.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>

        {period === "custom" && (
          <div className="flex items-center gap-2 text-xs">
            <label className="flex items-center gap-1 text-neutral-400">
              <span>From</span>
              <input
                type="datetime-local"
                className={`${selectBase} ${RANGE_W}`}
                value={fromISO}
                onChange={(e) => setFromISO(e.target.value)}
                title="UTC ISO (local input will be converted by browser)"
              />
            </label>
            <label className="flex items-center gap-1 text-neutral-400">
              <span>To</span>
              <input
                type="datetime-local"
                className={`${selectBase} ${RANGE_W}`}
                value={toISO}
                onChange={(e) => setToISO(e.target.value)}
                title="UTC ISO (local input will be converted by browser)"
              />
            </label>
            <button
              className="h-8 rounded-md border border-neutral-700 bg-neutral-800/70 px-2 text-xs text-neutral-200"
              onClick={() => void fetchSummary()}
              type="button"
              title="Refresh custom range"
            >
              Apply
            </button>
          </div>
        )}

        {/* reserved space for hint/loading/error to avoid width shifts */}
        <div className={`ml-auto text-[11px] text-neutral-500 text-right ${HINT_W}`}>
          {pnlLoading
            ? "Loading summary…"
            : pnlError
            ? <span className="text-rose-400">{pnlError}</span>
            : `Period: ${periodLabel(period)}`}
        </div>
      </div>

      {/* Metrics Grid */}
      <div className={gridCls}>
        <div className="rounded-xl bg-neutral-800/70 p-3 border border-neutral-800">
          <div className="text-xs text-neutral-400">Exposure</div>
          <div className={compact ? "text-sm font-semibold text-neutral-100" : "text-base font-semibold text-neutral-100"}>
            {fmtUsd(exposure)}
          </div>
        </div>

        <div className="rounded-xl bg-neutral-800/70 p-3 border border-neutral-800">
          <div className="text-xs text-neutral-400">uPnL</div>
          <div className={valueCls(uPnL >= 0)}>{fmtUsd(uPnL)}</div>
        </div>

        <div className="rounded-xl bg-neutral-800/70 p-3 border border-neutral-800">
          <div className="text-xs text-neutral-400">
            rPnL ({period === "custom" ? "custom" : period})
          </div>
          <div className={valueCls(rPnL >= 0)}>{fmtUsd(rPnL)}</div>
        </div>
      </div>
    </div>
  );
}
