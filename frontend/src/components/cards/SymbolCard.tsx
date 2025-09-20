import { useState, useMemo, useRef } from "react";
import cx from "classnames";
import { useShallow } from "zustand/react/shallow";

import { useToast } from "@/hooks/useToast";
import { getErrorMessage } from "@/lib/errors";

import { useSymbols } from "@/store/symbols";
import { useStrategy } from "@/store/strategy";
import { useMarket } from "@/store/market";
import type { StoreQuote } from "@/store/market";
import { useMetrics } from "@/store/metrics";
import { useOrders } from "@/store/orders";

import {
  apiStartSymbols,
  apiStopSymbols,
  apiFlatten,
  apiPlaceOrder,
  apiGetPositions,
  apiGetUISnapshot,
} from "@/api/api";

import DepthGlass from "@/components/charts/DepthGlass";
import OrdersFills from "@/components/cards/OrdersFills";
import type { UISnapshot } from "@/types/api";

type Props = { symbol: string };

const EMPTY_TAPE: ReadonlyArray<{ ts: number; mid: number; spread_bps?: number }> =
  Object.freeze([]);

/* ------------------------------ small ui bits ------------------------------ */
function StatusBadge({ running }: { running: boolean }) {
  return (
    <div
      className={cx(
        "rounded-md px-2 py-0.5 text-xs",
        running ? "bg-emerald-600/20 text-emerald-300" : "bg-zinc-700/60 text-zinc-300"
      )}
    >
      {running ? "RUNNING" : "STOPPED"}
    </div>
  );
}

function QuotePanel({ bid, ask, spreadBps }: { bid?: number; ask?: number; spreadBps?: number }) {
  return (
    <div className="rounded-xl border border-zinc-700/60 p-2 text-sm">
      <div className="mb-1 font-medium text-zinc-400">Quote</div>
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-lg bg-zinc-900/60 p-2">
          <div className="text-xs text-zinc-500">Bid</div>
          <div className="text-base font-mono tabular-nums">{bid ?? "—"}</div>
        </div>
        <div className="rounded-lg bg-zinc-900/60 p-2">
          <div className="text-xs text-zinc-500">Ask</div>
          <div className="text-base font-mono tabular-nums">{ask ?? "—"}</div>
        </div>
        <div className="col-span-2 rounded-lg bg-zinc-900/60 p-2">
          <div className="text-xs text-zinc-500">Spread (bps)</div>
          <div className="text-base font-mono tabular-nums">
            {typeof spreadBps === "number" ? spreadBps.toFixed(2) : "—"}
          </div>
        </div>
      </div>
    </div>
  );
}

function PositionPanel({
  qty = 0,
  avg = 0,
  uPnL = 0,
  rPnL = 0,
}: {
  qty?: number;
  avg?: number;
  uPnL?: number;
  rPnL?: number;
}) {
  const safeFix = (v: number, digits = 4) => (Number.isFinite(v) ? v.toFixed(digits) : "—");
  return (
    <div className="rounded-xl border border-zinc-700/60 p-2">
      <div className="text-xs text-zinc-500">Position</div>
      <div className="mt-1 grid grid-cols-6 gap-1 text-sm">
        <div className="col-span-3 rounded-md bg-zinc-900/40 px-2 py-1">
          <div className="text-[11px] text-zinc-500">Qty</div>
          <div className="font-mono tabular-nums">{qty}</div>
        </div>
        <div className="col-span-3 rounded-md bg-zinc-900/40 px-2 py-1">
          <div className="text-[11px] text-zinc-500">Avg</div>
          <div className="font-mono tabular-nums truncate">{safeFix(avg, 6)}</div>
        </div>
        <div className="col-span-6 rounded-md bg-zinc-900/40 px-2 py-1">
          <div className="text-[10px] text-zinc-400 whitespace-nowrap">uPnL / rPnL</div>
          <div className="font-mono tabular-nums whitespace-nowrap overflow-hidden text-ellipsis">
            {safeFix(uPnL, 6)} / {safeFix(rPnL, 6)}
          </div>
        </div>
      </div>
    </div>
  );
}

function QuickTrade({
  symbol,
  bid,
  ask,
  enqueue,
  selectedPrice,
  onClearSelected,
}: {
  symbol: string;
  bid?: number;
  ask?: number;
  enqueue: <T>(op: () => Promise<T>) => Promise<T>;
  selectedPrice?: number | null;
  onClearSelected?: () => void;
}) {
  const toast = useToast();
  const [qty, setQty] = useState<number>(0);

  const mid = useMemo(() => {
    if (typeof bid === "number" && typeof ask === "number" && bid > 0 && ask > 0) {
      return (bid + ask) / 2;
    }
    return bid ?? ask ?? 0;
  }, [bid, ask]);

  const priceToUse = Number.isFinite(selectedPrice ?? NaN) ? (selectedPrice as number) : mid;

  const place = async (side: "BUY" | "SELL") => {
    try {
      if (!qty || qty <= 0) {
        toast.error("Qty must be > 0");
        return;
      }
      await enqueue(async () => {
        await apiPlaceOrder({ symbol, side, qty, price: priceToUse, tag: "manual" });
      });
      toast.success(`${symbol} ${side} placed`);
    } catch (err) {
      toast.error(getErrorMessage(err), "Order failed");
    }
  };

  return (
    <div className="rounded-xl border border-zinc-700/60 p-2">
      <div className="mb-1 flex items-center justify-between">
        <div className="font-medium text-zinc-400">Quick trade</div>
        {Number.isFinite(selectedPrice ?? NaN) && (
          <div className="flex items-center gap-2 text-[11px]">
            <span className="rounded bg-zinc-900/60 px-1.5 py-0.5 font-mono text-zinc-300">
              {priceToUse.toFixed(6)}
            </span>
            <button
              onClick={onClearSelected}
              className="text-zinc-400 hover:text-zinc-200"
              title="Reset selected price"
              type="button"
            >
              ×
            </button>
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="number"
          min={0}
          step="any"
          value={Number.isFinite(qty) ? qty : 0}
          onChange={(e) => setQty(parseFloat(e.target.value || "0"))}
          className="w-20 md:w-18 rounded-md border border-zinc-700 bg-zinc-900/60 px-2 py-1 text-sm outline-none text-center"
          placeholder="Qty"
        />
        <button
          onClick={() => place("BUY")}
          className="h-8 px-3 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-500 shrink-0"
          title="Buy (paper)"
          type="button"
        >
          Buy
        </button>
        <button
          onClick={() => place("SELL")}
          className="h-8 px-3 text-sm rounded-lg bg-rose-600 hover:bg-rose-500 shrink-0"
          title="Sell (paper)"
          type="button"
        >
          Sell
        </button>
      </div>
    </div>
  );
}

/* ------------------------------ main card ------------------------------ */
export default function SymbolCard({ symbol }: Props) {
  const toast = useToast();

  // symbols store
  const remove = useSymbols((s) => s.remove);
  const running = useSymbols((s) => s.items.find((x) => x.symbol === symbol)?.running ?? false);

  // strategy store
  const start = useStrategy((s) => s.start);
  const stop = useStrategy((s) => s.stop);
  const busy = useStrategy((s) => s.busy);

  // orders store
  const setOrdersFromSnapshot = useOrders((s) => s.setFromSnapshot);

  // market store
  const { qq, pos, tape } = useMarket(
    useShallow((s) => ({
      qq: s.quotes[symbol] as StoreQuote | undefined,
      pos: s.positions[symbol],
      tape: s.tape?.[symbol] ?? EMPTY_TAPE,
    }))
  );

  // metrics store
  const entries = useMetrics((s) => s.entriesOf(symbol));
  const exitsTP = useMetrics((s) => s.exitsTPOf(symbol));
  const exitsSL = useMetrics((s) => s.exitsSLOf(symbol));
  const exitsTIMEOUT = useMetrics((s) => s.exitsTIMEOUTOf(symbol));
  const openFlag = useMetrics((s) => s.openFlagOf(symbol));
  const realized = useMetrics((s) => s.realizedOf(symbol));

  // methods
  const setPositions = useMarket((s) => s.setPositions);

  /* --------------------- per-symbol "saga" queue --------------------- */
  const chainRef = useRef<Promise<unknown>>(Promise.resolve());

  const refreshAfterAction = async () => {
    const [positions, snap] = await Promise.all([
      apiGetPositions([symbol]),
      apiGetUISnapshot(["orders", "fills"]),
    ]);
    setPositions(positions);
    setOrdersFromSnapshot({
      orders: (snap as UISnapshot).orders ?? [],
      fills: (snap as UISnapshot).fills ?? [],
    });
  };

  const enqueue = async <T,>(op: () => Promise<T>): Promise<T> => {
    let resolveFn!: (v: T) => void;
    let rejectFn!: (e: unknown) => void;
    const next = new Promise<T>((resolve, reject) => {
      resolveFn = resolve;
      rejectFn = reject;
    });

    chainRef.current = chainRef.current
      .then(op)
      .then(async (res) => {
        await refreshAfterAction();
        resolveFn(res);
      })
      .catch((e) => {
        rejectFn(e);
      });

    return next;
  };

  const onStart = async () => {
    try {
      await enqueue(async () => {
        await apiStartSymbols([symbol]);
        await start([symbol]);
      });
      toast.success(`${symbol} started`);
    } catch (err) {
      toast.error(getErrorMessage(err), "Start failed");
    }
  };

  const onStop = async () => {
    try {
      await enqueue(async () => {
        await apiStopSymbols([symbol], false);
        await stop([symbol], false);
      });
      toast.info(`${symbol} stopped (no flatten)`);
    } catch (err) {
      toast.error(getErrorMessage(err), "Stop failed");
    }
  };

  const onFlatten = async () => {
    try {
      await enqueue(async () => {
        await apiFlatten(symbol);
      });
      toast.info(`${symbol} flattened`);
    } catch (err) {
      toast.error(getErrorMessage(err), "Flatten failed");
    }
  };

  /* ---------------------- pick price ---------------------- */
  const [selectedPrice, setSelectedPrice] = useState<number | null>(null);

  return (
    <div
      className="rounded-2xl border border-zinc-800/50 bg-zinc-900/40 shadow-lg p-3
                flex flex-col h-[820px] min-h-[820px]"
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 pt-4 pb-2">
        <div className="text-lg font-semibold tracking-wide">{symbol}</div>
        <StatusBadge running={running} />
        <div className="ml-auto flex items-center gap-2">
          {running ? (
            <button
              onClick={onStop}
              className="h-8 px-3 text-sm rounded-lg bg-zinc-700 hover:bg-zinc-600 disabled:opacity-60"
              disabled={busy}
              title="Stop this symbol"
              type="button"
            >
              ⏹ Stop
            </button>
          ) : (
            <button
              onClick={onStart}
              className="h-8 px-3 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-60"
              disabled={busy}
              title="Start this symbol"
              type="button"
            >
              ▶ Start
            </button>
          )}
          <button
            onClick={onFlatten}
            className="h-8 px-3 text-sm rounded-lg bg-zinc-700 text-zinc-200/90 hover:bg-zinc-600 disabled:opacity-60"
            disabled={busy}
            title="Flatten (close position)"
            type="button"
          >
            🧹 Flatten
          </button>
          <button
            onClick={() => remove(symbol)}
            className="h-8 px-2 text-sm rounded-lg bg-zinc-700/60 text-zinc-200 hover:bg-zinc-600"
            title="Remove card"
            type="button"
          >
            ×
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="px-4 pb-3 flex-1">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 h-full">
          {/* LEFT column */}
          <div className="flex flex-col gap-3 h-full">
            <DepthGlass
              bid={qq?.bid}
              ask={qq?.ask}
              bids={qq?.bids}
              asks={qq?.asks}
              positionPrice={pos?.avg_price ?? null}
              tape={tape}
              className="flex-1"
            />
          </div>

          {/* RIGHT column */}
          <div className="flex flex-col gap-3">
            <QuickTrade
              symbol={symbol}
              bid={qq?.bid}
              ask={qq?.ask}
              enqueue={enqueue}
              selectedPrice={selectedPrice ?? undefined}
              onClearSelected={() => setSelectedPrice(null)}
            />
            <PositionPanel
              qty={pos?.qty ?? 0}
              avg={pos?.avg_price ?? 0}
              uPnL={pos?.unrealized_pnl ?? 0}
              rPnL={pos?.realized_pnl ?? 0}
            />
            <QuotePanel bid={qq?.bid} ask={qq?.ask} spreadBps={qq?.spread_bps} />
          </div>

          <div className="md:col-span-2 rounded-xl border border-zinc-700/60 p-2">
            <div className="mb-1 font-medium text-zinc-400">Orders / Fills</div>
            <OrdersFills symbol={symbol} limit={10} />
          </div>
        </div>
      </div>

      {/* Footer metrics */}
      <div className="border-t border-zinc-800/60 bg-zinc-900/30 px-4 py-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
        <div className="rounded-lg bg-zinc-900/60 p-2">
          <div className="text-xs text-zinc-500">Entries</div>
          <div className="font-mono tabular-nums">{entries}</div>
        </div>
        <div className="rounded-lg bg-zinc-900/60 p-2">
          <div className="text-xs text-zinc-500">Exits (TP/SL/TO)</div>
          <div className="font-mono tabular-nums">
            {exitsTP} / {exitsSL} / {exitsTIMEOUT}
          </div>
        </div>
        <div className="rounded-lg bg-zinc-900/60 p-2">
          <div className="text-xs text-zinc-500">Open</div>
          <div className="font-mono tabular-nums">{openFlag}</div>
        </div>
        <div className="rounded-lg bg-zinc-900/60 p-2">
          <div className="text-xs text-zinc-500">Realized PnL</div>
          <div className="font-mono tabular-nums">
            {Number.isFinite(realized) ? (realized as number).toFixed(4) : realized}
          </div>
        </div>
      </div>
    </div>
  );
}
