// src/components/cards/PositionsTable.tsx
import { useCallback, useMemo, useRef, useState } from "react";
import cx from "classnames";

import { usePositions, type UsePositionsResult } from "@/hooks/usePositions";
import { usePositionsStore } from "@/store/positions";
import { type Position } from "@/types/index";
import { useToast } from "@/hooks/useToast";
import FlattenButton from "@/components/controls/FlattenButton";
import { useMarket } from "@/store/market";
import { useSymbols } from "@/store/symbols";
import SymbolPnlModal from "@/components/modals/SymbolPnlModal";
import http from "@/lib/http";

type MarkGetter = (symbol: string) => number | undefined;

export type PositionsTableProps = {
  symbols?: string[];
  intervalMs?: number;
  /** Layout style. 'sidebar' uses a fixed-height, scrollable body (3 rows). */
  variant?: "inline" | "sidebar";
  className?: string;
};

const ROWS_VIEWPORT = 3;
const ROW_PX = 40;
const HEADER_PX = 36;
const BODY_PX = ROWS_VIEWPORT * ROW_PX;

const norm = (s: string) => (s || "").trim().toUpperCase();

function computeUPnL(p: Position, mark?: number): number {
  if (!Number.isFinite(mark ?? NaN)) return 0;
  if (!Number.isFinite(p.avg_price ?? NaN)) return 0;
  if (!Number.isFinite(p.qty)) return 0;
  return ((mark as number) - (p.avg_price as number)) * p.qty;
}
function computeValueUSD(p: Position, mark?: number): number {
  if (!Number.isFinite(mark ?? NaN)) return 0;
  if (!Number.isFinite(p.qty)) return 0;
  return Math.abs(p.qty) * (mark as number);
}
function fmtNum(n: number, fractionDigits = 4): string {
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: fractionDigits,
  });
}
function fmtUsdSigned(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
function fmtUsdAbs(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  return `$${abs.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function PositionsTable({
  symbols,
  intervalMs = 3000,
  variant = "sidebar",
  className,
}: PositionsTableProps) {
  const { positions, loading, error, refresh }: UsePositionsResult = usePositions({
    symbols,
    intervalMs,
    immediate: true,
    // avoid “stuck loading” on reload when the tab restores in hidden state
    pauseWhenHidden: false,
  });

  const ensureSymbols = useSymbols((s) => s.ensureSymbols);
  const openCard = useCallback(
    (symbol: string) => {
      const sym = norm(symbol);
      if (sym) ensureSymbols([sym]);
    },
    [ensureSymbols]
  );

  // ---------- stable mark() with last-known fallback ----------
  const quoteOf = useMarket((s) => s.quoteOf);
  const quotesTick = useMarket((s) => s.quotesTick);
  const lastMarkRef = useRef<Record<string, number>>({});

  const markOf = useCallback<MarkGetter>(
    (symbol: string) => {
      const sym = norm(symbol);
      const q = quoteOf(sym);
      const mid = typeof q?.mid === "number" && q.mid > 0 ? q.mid : undefined;
      const bid = typeof q?.bid === "number" && q.bid > 0 ? q.bid : undefined;
      const ask = typeof q?.ask === "number" && q.ask > 0 ? q.ask : undefined;

      const calc = mid ?? (bid && ask ? (bid + ask) / 2 : bid ?? ask);
      if (typeof calc === "number" && Number.isFinite(calc) && calc > 0) {
        lastMarkRef.current[sym] = calc;
        return calc;
      }
      return lastMarkRef.current[sym];
    },
    [quoteOf]
  );

  // ---------- stable row order ----------
  const symbolsNorm = useMemo(
    () => (symbols?.length ? symbols.map((s) => norm(s)) : []),
    [symbols]
  );

  const orderMap = useMemo(() => {
    const m = new Map<string, number>();
    symbolsNorm.forEach((s, i) => m.set(s, i));
    return m;
  }, [symbolsNorm]);

  const rows: Position[] = useMemo(() => {
    const base = orderMap.size
      ? positions.filter((p) => orderMap.has(norm(p.symbol)))
      : positions.slice();

    base.sort((a, b) => {
      const as = norm(a.symbol);
      const bs = norm(b.symbol);
      if (orderMap.size) {
        const ia = orderMap.get(as) ?? Number.MAX_SAFE_INTEGER;
        const ib = orderMap.get(bs) ?? Number.MAX_SAFE_INTEGER;
        if (ia !== ib) return ia - ib;
        return as.localeCompare(bs);
      }
      return as.localeCompare(bs);
    });

    // only active positions
    return base.filter((p) => Number.isFinite(p.qty) && Math.abs(p.qty) > 0);
  }, [positions, orderMap]);

  // ---------- totals (active only) ----------
  const totalUPnL = useMemo(() => {
    void quotesTick; // recompute on new quotes
    return rows.reduce((t, p) => t + computeUPnL(p, markOf(p.symbol)), 0);
  }, [rows, markOf, quotesTick]);

  const totalRPnL = useMemo(
    () =>
      rows.reduce(
        (t, p) => t + (Number.isFinite(p.realized_pnl ?? NaN) ? (p.realized_pnl as number) : 0),
        0
      ),
    [rows]
  );

  const totalExposure = useMemo(() => {
    void quotesTick; // recompute on new quotes
    let t = 0;
    for (const p of rows) {
      const mark = markOf(p.symbol);
      if (typeof mark === "number" && Number.isFinite(mark) && Number.isFinite(p.qty)) {
        t += Math.abs(p.qty) * mark;
      }
    }
    return t;
  }, [rows, markOf, quotesTick]);

  // ---------- per-row busy for Flatten ----------
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const { push } = useToast();
  const upsert = usePositionsStore((s) => s.upsert);

  const onFlatten = useCallback(
  async (symbol: string) => {
    const sym = norm(symbol);
    try {
      setBusy((prev) => {
        const next = new Set(prev);
        next.add(sym);
        return next;
      });

      await http.post(`/api/exec/flatten/${encodeURIComponent(sym)}`, null, {
        headers: {
          "X-Idempotency-Key":
            (globalThis.crypto?.randomUUID?.() ?? String(Math.random())).toString(),
        },
      });

        push("success", `Orders placed to flatten ${sym}`, "Flattened");
        await refresh();

        const p = rows.find((x) => norm(x.symbol) === sym);
        if (p) upsert({ ...p, qty: 0 });
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Unknown error";
        push("error", msg, "Flatten error");
      } finally {
        setBusy((prev) => {
          const next = new Set(prev);
          next.delete(sym);
          return next;
        });
      }
    },
    [rows, refresh, push, upsert]
  );

  // ---------- modal ----------
  const [detailSymbol, setDetailSymbol] = useState<string | null>(null);
  const openDetails = useCallback((sym: string) => setDetailSymbol(sym), []);
  const closeDetails = useCallback(() => setDetailSymbol(null), []);

  // ---------- layout presets ----------
  const outerCls = cx(
    variant === "sidebar"
      ? "p-0 bg-transparent border-0 shadow-none"
      : "rounded-2xl border border-neutral-800 bg-neutral-900/60 shadow-lg p-4",
    className
  );

  const headerPad = variant === "sidebar" ? "px-3 pt-2 pb-2" : "pb-3";
  const footerPad =
    variant === "sidebar" ? "px-3 py-2 border-t border-neutral-800/70" : "mt-3";

  // ✅ memoize body rows to avoid re-creating function every render
  const bodyRows = useMemo(() => {
    if (rows.length === 0) {
      return (
        <tr>
          <td colSpan={8} className="px-3 py-8 text-center text-neutral-500">
            {loading ? "Loading positions…" : "No open positions"}
          </td>
        </tr>
      );
    }

    return rows.map((p) => {
      const sym = norm(p.symbol);
      const mark = markOf(sym);
      const u = computeUPnL(p, mark);
      const r = typeof p.realized_pnl === "number" ? p.realized_pnl : 0;
      const val = computeValueUSD(p, mark);
      const disableFlatten = busy.has(sym) || Math.abs(p.qty) === 0;

      return (
        <tr key={sym} className="border-t border-neutral-800/70">
          <td className="px-3 py-2 font-medium">
            <button
              type="button"
              onClick={() => openCard(sym)}
              className="underline decoration-dotted underline-offset-2 hover:text-neutral-50 hover:decoration-solid focus:outline-none focus:ring-1 focus:ring-neutral-600 rounded-sm cursor-pointer"
              title="Open card for this symbol"
            >
              {sym}
            </button>
          </td>
          <td className="px-3 py-2 text-right">{fmtNum(p.qty)}</td>
          <td className="px-3 py-2 text-right">{fmtNum(p.avg_price ?? NaN, 6)}</td>
          <td className="px-3 py-2 text-right">{fmtNum(mark ?? NaN, 6)}</td>
          <td className="px-3 py-2 text-right">{fmtUsdAbs(val)}</td>
          <td className={cx("px-3 py-2 text-right", u >= 0 ? "text-emerald-400" : "text-red-400")}>
            {fmtUsdSigned(u)}
          </td>
          <td className={cx("px-3 py-2 text-right", r >= 0 ? "text-emerald-400" : "text-red-400")}>
            {fmtUsdSigned(r)}
          </td>
          <td className="px-3 py-2 text-right">
            <div className="inline-flex items-center gap-2">
              <button
                type="button"
                onClick={() => openDetails(sym)}
                className="px-2 py-1 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-[11px] text-neutral-200"
                title="View PnL details"
              >
                Details
              </button>
              <FlattenButton
                label={busy.has(sym) ? "Flatten…" : "Flatten"}
                symbol={sym}
                disabled={disableFlatten}
                onConfirm={onFlatten}
                size="sm"
              />
            </div>
          </td>
        </tr>
      );
    });
  }, [rows, loading, markOf, busy, openCard, onFlatten, openDetails]);

  return (
    <>
      <aside className={outerCls}>
        {/* Header */}
        <div className={cx("flex items-center justify-between", headerPad)}>
          <h3 className="text-base font-semibold text-neutral-100">Positions</h3>
          <div className="text-xs md:text-sm text-neutral-400 space-x-4">
            <span>
              Exposure: <strong className="text-neutral-100">{fmtUsdAbs(totalExposure)}</strong>
            </span>
            <span>
              uPnL:{" "}
              <strong className={totalUPnL >= 0 ? "text-emerald-400" : "text-red-400"}>
                {fmtUsdSigned(totalUPnL)}
              </strong>
            </span>
            <span>
              rPnL:{" "}
              <strong className={totalRPnL >= 0 ? "text-emerald-400" : "text-red-400"}>
                {fmtUsdSigned(totalRPnL)}
              </strong>
            </span>
          </div>
        </div>

        {error && (
          <div className="mx-3 mb-2 rounded-md bg-red-900/40 text-red-300 px-3 py-2 text-sm border border-red-800">
            {error}
          </div>
        )}

        {/* Table */}
        {variant === "sidebar" ? (
          <div
            className="px-3 overflow-y-auto border border-neutral-800 rounded-md"
            style={{ height: BODY_PX + HEADER_PX, minHeight: BODY_PX + HEADER_PX }}
          >
            <table className="min-w-full text-xs md:text-sm text-neutral-200 table-fixed">
              <thead className="sticky top-0 z-10 bg-neutral-900 text-neutral-400 border-b border-neutral-800">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Symbol</th>
                  <th className="px-3 py-2 text-right font-medium">Qty</th>
                  <th className="px-3 py-2 text-right font-medium">Avg</th>
                  <th className="px-3 py-2 text-right font-medium">Mark</th>
                  <th className="px-3 py-2 text-right font-medium">Value (USDT)</th>
                  <th className="px-3 py-2 text-right font-medium">uPnL</th>
                  <th className="px-3 py-2 text-right font-medium">rPnL</th>
                  <th className="px-3 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>{bodyRows}</tbody>
            </table>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs md:text-sm text-neutral-200">
              <thead className="sticky top-0 z-10 bg-neutral-900/80 backdrop-blur text-neutral-400 border-b border-neutral-800">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Symbol</th>
                  <th className="px-3 py-2 text-right font-medium">Qty</th>
                  <th className="px-3 py-2 text-right font-medium">Avg</th>
                  <th className="px-3 py-2 text-right font-medium">Mark</th>
                  <th className="px-3 py-2 text-right font-medium">Value (USDT)</th>
                  <th className="px-3 py-2 text-right font-medium">uPnL</th>
                  <th className="px-3 py-2 text-right font-medium">rPnL</th>
                  <th className="px-3 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>{bodyRows}</tbody>
            </table>
          </div>
        )}

        {/* Footer */}
        <div className={footerPad}>
          <button
            onClick={() => void refresh()}
            className="w-full md:w-auto px-3 py-1.5 rounded-xl border border-neutral-700 hover:bg-neutral-800 transition text-xs md:text-sm text-neutral-200"
            disabled={loading}
            title="Refresh positions now"
          >
            Refresh
          </button>
        </div>
      </aside>

      {/* Modal */}
      <SymbolPnlModal open={!!detailSymbol} symbol={detailSymbol} onClose={closeDetails} />
    </>
  );
}
