// src/components/charts/DepthLadder.tsx
import { memo, useMemo } from "react";
import cx from "classnames";
import type { Level } from "@/types/api";

type Props = {
  bids?: Level[];
  asks?: Level[];
  levels?: number;
  className?: string;
  rowHeight?: number;
  priceDigits?: number;
  onPriceClick?: (price: number, side: "bid" | "ask") => void;
};

function fmtNum(n: number, digits = 6) {
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}
function fmtQty(n: number) {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (abs >= 1_000) return (n / 1_000).toFixed(2) + "K";
  return String(n);
}

function sanitizeLevels(levels?: Level[]): Level[] {
  if (!Array.isArray(levels)) return [];
  const out: Level[] = [];
  for (const lv of levels) {
    if (!Array.isArray(lv)) continue;
    const [p, q] = lv;
    if (
      typeof p === "number" &&
      typeof q === "number" &&
      Number.isFinite(p) &&
      Number.isFinite(q) &&
      q > 0
    ) {
      out.push([p, q]);
    }
  }
  return out;
}

function DepthLadder({
  bids,
  asks,
  levels = 10,
  className,
  rowHeight = 26,
  priceDigits = 6,
  onPriceClick,
}: Props) {
  const bidRows = useMemo(() => {
    const xs = sanitizeLevels(bids);
    xs.sort((a, b) => b[0] - a[0]);
    return xs.slice(0, Math.max(1, levels));
  }, [bids, levels]);

  const askRows = useMemo(() => {
    const xs = sanitizeLevels(asks);
    xs.sort((a, b) => a[0] - b[0]);
    return xs.slice(0, Math.max(1, levels));
  }, [asks, levels]);

  const maxBidQty = useMemo(() => Math.max(1, ...bidRows.map((x) => x[1])), [bidRows]);
  const maxAskQty = useMemo(() => Math.max(1, ...askRows.map((x) => x[1])), [askRows]);

  const noL2 = bidRows.length === 0 && askRows.length === 0;

  return (
    <div
      className={cx(
        "relative rounded-xl border border-zinc-700/60 bg-zinc-900/50 overflow-hidden",
        className
      )}
      role="table"
      aria-label="Order book (top levels)"
    >
      <div className="flex items-center justify-between px-2 py-1.5 border-b border-zinc-800/60">
        <div className="text-sm text-zinc-300">Order book (L2)</div>
        <div className="text-[10px] text-zinc-500">top {levels} / side</div>
      </div>

      <div className="grid grid-cols-2 gap-0">
        {/* BIDS */}
        <div className="border-r border-zinc-800/60">
          <div className="px-2 py-1 text-[11px] text-emerald-300/80">Bids</div>
          <ul className="px-2 pb-2" role="rowgroup">
            {bidRows.map(([price, qty], i) => {
              const w = Math.min(100, Math.max(3, (qty / maxBidQty) * 100));
              return (
                <li
                  key={`bid-${i}-${price}`}
                  role="row"
                  className="relative flex items-center gap-2 rounded-md overflow-hidden my-0.5"
                  style={{ height: rowHeight }}
                >
                  <div
                    className="absolute left-0 top-0 bottom-0 bg-emerald-600/30"
                    style={{ width: `${w}%` }}
                    aria-hidden
                  />
                  <div className="relative z-10 flex w-full items-center justify-between text-sm">
                    <button
                      type="button"
                      onClick={onPriceClick ? () => onPriceClick(price, "bid") : undefined}
                      className={cx(
                        "font-mono tabular-nums hover:underline decoration-emerald-400 text-emerald-200",
                        onPriceClick ? "cursor-pointer" : "cursor-default"
                      )}
                      title="Use this price"
                    >
                      {fmtNum(price, priceDigits)}
                    </button>
                    <span className="font-mono tabular-nums text-zinc-200">{fmtQty(qty)}</span>
                  </div>
                </li>
              );
            })}
            {bidRows.length === 0 && (
              <li className="h-10 flex items-center text-[12px] text-zinc-500">No bid levels</li>
            )}
          </ul>
        </div>

        {/* ASKS */}
        <div>
          <div className="px-2 py-1 text-[11px] text-rose-300/90">Asks</div>
          <ul className="px-2 pb-2" role="rowgroup">
            {askRows.map(([price, qty], i) => {
              const w = Math.min(100, Math.max(3, (qty / maxAskQty) * 100));
              return (
                <li
                  key={`ask-${i}-${price}`}
                  role="row"
                  className="relative flex items-center gap-2 rounded-md overflow-hidden my-0.5"
                  style={{ height: rowHeight }}
                >
                  <div
                    className="absolute right-0 top-0 bottom-0 bg-rose-600/30"
                    style={{ width: `${w}%` }}
                    aria-hidden
                  />
                  <div className="relative z-10 flex w-full items-center justify-between text-sm">
                    <span className="font-mono tabular-nums text-zinc-200">{fmtQty(qty)}</span>
                    <button
                      type="button"
                      onClick={onPriceClick ? () => onPriceClick(price, "ask") : undefined}
                      className={cx(
                        "font-mono tabular-nums hover:underline decoration-rose-400 text-rose-200",
                        onPriceClick ? "cursor-pointer" : "cursor-default"
                      )}
                      title="Use this price"
                    >
                      {fmtNum(price, priceDigits)}
                    </button>
                  </div>
                </li>
              );
            })}
            {askRows.length === 0 && (
              <li className="h-10 flex items-center text-[12px] text-zinc-500">No ask levels</li>
            )}
          </ul>
        </div>
      </div>

      {noL2 && (
        <div className="absolute inset-0 flex items-center justify-center bg-zinc-900/40 backdrop-blur-[1px]">
          <div className="px-2 py-1 text-[12px] text-zinc-400 bg-zinc-900/70 rounded border border-zinc-700/50">
            Waiting for L2 levels…
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(DepthLadder);
