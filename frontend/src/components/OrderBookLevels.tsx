// src/components/OrderBookLevels.tsx
import React, { useMemo } from "react";

// local helper (not exported) to avoid cross-module type mismatches
type Lvl = readonly [number, number];

const fmt = (v: number, dp: number) =>
  Number.isFinite(v) ? v.toFixed(dp) : "—";

export function OrderBookLevels({
  side,
  levels,
  priceDp = 5,
  qtyDp = 2,
  rows = 10,
  onPriceClick,
}: {
  side: "bid" | "ask";
  levels?: ReadonlyArray<readonly [number, number]>; // <— accepts readonly tuples
  priceDp?: number;
  qtyDp?: number;
  rows?: number;
  onPriceClick?: (price: number) => void;
}) {
  const color = side === "bid" ? "text-emerald-300" : "text-rose-300";
  const display = useMemo<Lvl[]>(
    () => (levels ? Array.from(levels).slice(0, rows) : []),
    [levels, rows]
  );

  return (
    <div className={`flex flex-col text-xs tabular-nums ${color}`}>
      {display.map(([price, qty], i) => (
        <button
          key={i}
          type="button"
          onClick={() => onPriceClick?.(price)}
          className="flex items-center justify-between leading-5 hover:bg-zinc-900/40 rounded px-1 text-left"
        >
          <span className="w-20 text-right">{fmt(qty as number, qtyDp)}</span>
          <span className="px-1 opacity-60">@</span>
          <span className="w-24">{fmt(price as number, priceDp)}</span>
        </button>
      ))}
    </div>
  );
}

export function OrderBookLevelsPanel({
  bids,
  asks,
  rows = 10,
  priceDp = 5,
  qtyDp = 2,
  onPriceClick,
}: {
  bids?: ReadonlyArray<readonly [number, number]>; // <— readonly tuples
  asks?: ReadonlyArray<readonly [number, number]>;
  rows?: number;
  priceDp?: number;
  qtyDp?: number;
  onPriceClick?: (price: number) => void;
}) {
  return (
    <div className="rounded-xl border border-zinc-700/60 p-2">
      <div className="mb-1 flex items-center justify-between text-sm text-zinc-400">
        <div className="font-medium">Order book (L2)</div>
        <div className="opacity-60">top {rows} / side</div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="mb-1 text-xs text-emerald-300/80">Bids</div>
          <OrderBookLevels
            side="bid"
            levels={bids}
            rows={rows}
            priceDp={priceDp}
            qtyDp={qtyDp}
            onPriceClick={onPriceClick}
          />
        </div>
        <div>
          <div className="mb-1 text-xs text-rose-300/80">Asks</div>
          <OrderBookLevels
            side="ask"
            levels={asks}
            rows={rows}
            priceDp={priceDp}
            qtyDp={qtyDp}
            onPriceClick={onPriceClick}
          />
        </div>
      </div>
    </div>
  );
}
