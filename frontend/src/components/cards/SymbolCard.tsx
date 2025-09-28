// src/components/cards/SymbolCard.tsx
import { useState, useMemo, useRef, useEffect } from "react";
import cx from "classnames";

import { useToast } from "@/hooks/useToast";
import { getErrorMessage } from "@/lib/errors";

import { useSymbols } from "@/store/symbols";
import { useStrategy } from "@/store/strategy";
import { useMarket } from "@/store/market";
import type { StoreQuote, TapeItem } from "@/store/market";
import { useMetrics } from "@/store/metrics";
import { useOrders } from "@/store/orders";

import {
  apiStartSymbols,
  apiStopSymbols,
  apiFlatten,
  apiPlaceOrder,
  apiGetExecPositions,
  apiGetUISnapshot,
} from "@/api/api";
import type { StrategyStartResponse, StrategyStopResponse } from "@/api/api";

import DepthGlass from "@/components/charts/DepthGlass";
import OrdersFills from "@/components/cards/OrdersFills";
import type { UISnapshot } from "@/types/api";

type Props = { symbol: string };

const EMPTY_TAPE: ReadonlyArray<TapeItem> = Object.freeze([]);

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

function QuotePanel({
  bid,
  ask,
  spreadBps,
  onPickPrice,
}: {
  bid?: number;
  ask?: number;
  spreadBps?: number;
  onPickPrice?: (p: number) => void;
}) {
  const clickBid = () => {
    if (typeof bid === "number" && bid > 0 && onPickPrice) onPickPrice(bid);
  };
  const clickAsk = () => {
    if (typeof ask === "number" && ask > 0 && onPickPrice) onPickPrice(ask);
  };

  return (
    <div className="rounded-xl border border-zinc-700/60 p-2 text-sm">
      <div className="mb-1 font-medium text-zinc-400">Quote</div>
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={clickBid}
          className="rounded-lg bg-zinc-900/60 p-2 text-left hover:bg-zinc-900/80 transition"
          title="Use Bid as price"
        >
          <div className="text-xs text-zinc-500">Bid</div>
          <div className="text-base font-mono tabular-nums">{bid ?? "—"}</div>
        </button>
        <button
          type="button"
          onClick={clickAsk}
          className="rounded-lg bg-zinc-900/60 p-2 text-left hover:bg-zinc-900/80 transition"
          title="Use Ask as price"
        >
          <div className="text-xs text-zinc-500">Ask</div>
          <div className="text-base font-mono tabular-nums">{ask ?? "—"}</div>
        </button>
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

/* ------------------------- helpers (no any, no hooks) ------------------------- */
interface ErrEnvelope {
  error?: { message?: string };
  message?: string;
  detail?: unknown;
  msg?: string;
}
interface AxiosLikeError {
  response?: { status?: number; data?: unknown };
  message?: string;
}
function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}
function asErrEnvelope(x: unknown): ErrEnvelope | undefined {
  return isRecord(x) ? (x as ErrEnvelope) : undefined;
}
function extractDetailText(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts: string[] = [];
    for (const d of detail) {
      if (typeof d === "string") parts.push(d);
      else if (isRecord(d)) {
        const msg =
          (d as { msg?: unknown; message?: unknown }).msg ?? (d as { message?: unknown }).message;
        if (typeof msg === "string") parts.push(msg);
      }
    }
    return parts.length ? parts.join("; ") : undefined;
  }
  if (isRecord(detail)) {
    const obj = detail as { msg?: unknown; message?: unknown };
    const msg = obj.msg ?? obj.message;
    if (typeof msg === "string") return msg;
  }
  return undefined;
}

/* ------------------------------ QuickTrade ------------------------------ */
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

  const prettyApiError = (err: unknown): string => {
    const ax = err as AxiosLikeError;
    const status = ax.response?.status;
    const env = asErrEnvelope(ax.response?.data);
    const detailText = env ? extractDetailText(env.detail) : undefined;

    const message =
      env?.error?.message || env?.message || detailText || env?.msg || ax.message || "";

    if (/insufficient|balance|margin/i.test(message)) {
      return `Недостаточно средств для ордера по ${symbol}. Уменьшите размер позиции или пополните депозит.`;
    }
    if (status === 422) return message || "Неверные параметры (422). Проверьте размер и цену.";
    if (status && status >= 400) return message || `Ошибка сервера (${status}). Попробуйте ещё раз.`;
    return message || "Не удалось выполнить ордер. Попробуйте ещё раз.";
  };

  const mid = useMemo(() => {
    if (typeof bid === "number" && typeof ask === "number" && bid > 0 && ask > 0) {
      return (bid + ask) / 2;
    }
    return bid ?? ask ?? 0;
  }, [bid, ask]);

  const priceToUse = Number.isFinite(selectedPrice ?? NaN)
    ? (selectedPrice as number)
    : Number.isFinite(mid)
    ? mid
    : 0;

  const place = async (side: "BUY" | "SELL") => {
    if (!qty || qty <= 0) {
      toast.error("Qty must be > 0");
      return;
    }
    if (!priceToUse || priceToUse <= 0) {
      toast.error("No valid price (bid/ask/mid missing)");
      return;
    }
    try {
      await enqueue(async () => {
        await apiPlaceOrder({ symbol, side, qty, price: priceToUse, tag: "manual" });
      });
      toast.success(`${symbol} ${side} placed`);
    } catch (err) {
      toast.error(prettyApiError(err), "Order failed");
    }
  };

  const presets: ReadonlyArray<number> = [0.001, 0.005, 0.01, 0.05, 0.1];

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

      <div className="flex flex-col gap-2">
        {/* Qty row */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="h-8 w-8 rounded-md bg-zinc-800 hover:bg-zinc-700"
            title="Decrease"
            onClick={() =>
              setQty((q) => {
                const base = q || 0;
                const step = base > 0 ? base : 0.001;
                const next = Math.max(0, +(base - step).toFixed(6));
                return next;
              })
            }
          >
            −
          </button>
          <input
            type="number"
            min={0}
            step="any"
            value={Number.isFinite(qty) ? qty : 0}
            onChange={(e) => setQty(parseFloat(e.target.value || "0"))}
            className="w-28 rounded-md border border-zinc-700 bg-zinc-900/60 px-2 py-1 text-sm outline-none text-center font-mono"
            placeholder="Qty"
          />
          <button
            type="button"
            className="h-8 w-8 rounded-md bg-zinc-800 hover:bg-zinc-700"
            title="Increase"
            onClick={() =>
              setQty((q) => {
                const base = q || 0;
                const step = base > 0 ? base : 0.001;
                const next = +(base + step).toFixed(6);
                return next;
              })
            }
          >
            +
          </button>

          <div className="ml-2 flex flex-wrap gap-1">
            {presets.map((v) => (
              <button
                key={v}
                type="button"
                className="h-7 px-2 rounded-md bg-zinc-800/70 hover:bg-zinc-700 text-xs"
                onClick={() => setQty(v)}
                title={`Set qty ${v}`}
              >
                {v}
              </button>
            ))}
            <button
              type="button"
              className="h-7 px-2 rounded-md bg-zinc-800/70 hover:bg-zinc-700 text-xs"
              onClick={() => setQty(presets[presets.length - 1])}
              title="Max (demo)"
            >
              MAX
            </button>
          </div>
        </div>

        {/* Action row */}
        <div className="flex items-center gap-2">
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
    </div>
  );
}

/* ------------------------------ main card ------------------------------ */
export default function SymbolCard({ symbol }: Props) {
  const toast = useToast();

  const sym = symbol.toUpperCase(); // normalize for store lookups

  // symbols store
  const remove = useSymbols((s) => s.remove);
  const running = useSymbols((s) => s.items.find((x) => x.symbol === sym)?.running ?? false);

  // strategy store
  const start = useStrategy((s) => s.start);
  const stop = useStrategy((s) => s.stop);
  const busy = useStrategy((s) => s.busy);

  // orders store
  const setOrdersFromSnapshot = useOrders((s) => s.setFromSnapshot);

  // market store — lookups by UPPERCASE key
  const qq = useMarket((s) => s.quotes[sym] as StoreQuote | undefined);
  const pos = useMarket((s) => s.positions[sym]);
  const tape = useMarket((s) => s.tape?.[sym] ?? EMPTY_TAPE);

  // metrics store
  const entries = useMetrics((s) => s.entriesOf(sym));
  const exitsTP = useMetrics((s) => s.exitsTPOf(sym));
  const exitsSL = useMetrics((s) => s.exitsSLOf(sym));
  const exitsTIMEOUT = useMetrics((s) => s.exitsTIMEOUTOf(sym));
  const openFlag = useMetrics((s) => s.openFlagOf(sym));
  const realized = useMetrics((s) => s.realizedOf(sym));

  // methods
  const setPositions = useMarket((s) => s.setPositions);

  /* --------------------- per-symbol "saga" queue --------------------- */
  const chainRef = useRef<Promise<unknown>>(Promise.resolve());

  const refreshAfterAction = async () => {
    const [positions, snap] = await Promise.all([
      apiGetExecPositions([sym]),
      apiGetUISnapshot(["orders", "fills"]),
    ]);
    setPositions(positions);
    const snapshot: UISnapshot = snap;
    setOrdersFromSnapshot({
      orders: snapshot.orders ?? [],
      fills: snapshot.fills ?? [],
    });
  };

  // Initial hydrate for the card
  useEffect(() => {
    refreshAfterAction().catch(() => {
      /* non-blocking */
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sym]);

  const enqueue = async <T,>(op: () => Promise<T>): Promise<T> => {
    const next = chainRef.current
      .then(op)
      .finally(async () => {
        try {
          await refreshAfterAction();
        } catch {
          /* ignore refresh errors */
        }
      });

    chainRef.current = next.catch(() => {
      /* keep chain alive even if it errors */
    });

    return next as Promise<T>;
  };

  const onStart = async () => {
    try {
      await enqueue(async () => {
        const resp: StrategyStartResponse = await apiStartSymbols([sym]);
        await start([sym]);
        if (resp?.message) toast.info(resp.message);
      });
    } catch (err) {
      toast.error(getErrorMessage(err) || "Start failed", "Start failed");
      return;
    }
    toast.success(`${sym} started`);
  };

  const onStop = async () => {
    try {
      await enqueue(async () => {
        const resp: StrategyStopResponse = await apiStopSymbols([sym], false);
        await stop([sym], false);
        if (resp?.message) toast.info(resp.message);
      });
    } catch (err) {
      toast.error(getErrorMessage(err) || "Stop failed", "Stop failed");
      return;
    }
    toast.info(`${sym} stopped (no flatten)`);
  };

  const onFlatten = async () => {
    try {
      await enqueue(async () => {
        await apiFlatten(sym);
      });
    } catch (err) {
      toast.error(getErrorMessage(err) || "Flatten failed", "Flatten failed");
      return;
    }
    toast.info(`${sym} flattened`);
  };

  /* ----- settings trigger (opens global modal) ----- */
  const openSettings = () => {
    window.dispatchEvent(
      new CustomEvent("open-strategy-settings", {
        detail: { symbol: sym },
      })
    );
  };

  /* ---------------------- pick price ---------------------- */
  const [selectedPrice, setSelectedPrice] = useState<number | null>(null);

  return (
    <div
      className={cx(
        "relative flex flex-col rounded-2xl border border-zinc-800/50 bg-zinc-900/40 shadow-lg",
        // Single fixed height keeps grid stable. Width is responsive.
        "h-[860px]",
        // Clip sticky header/footer corners
        "overflow-hidden"
      )}
    >
      {/* Header (sticky inside the card) */}
      <div
        className="
          sticky top-0 z-10
          bg-zinc-900/80 supports-[backdrop-filter]:bg-zinc-900/60 backdrop-blur
          border-b border-zinc-800/60
          px-4 pt-4 pb-2 flex items-center gap-3
        "
      >
        <div className="text-lg font-semibold tracking-wide">{sym}</div>
        <StatusBadge running={running} />
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={openSettings}
            className="h-8 px-3 text-sm rounded-lg bg-zinc-700/70 hover:bg-zinc-600"
            title="Strategy settings"
            type="button"
          >
            ⚙️ Settings
          </button>

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
            onClick={() => remove(sym)}
            className="h-8 px-2 text-sm rounded-lg bg-zinc-700/60 text-zinc-200 hover:bg-zinc-600"
            title="Remove card"
            type="button"
          >
            ×
          </button>
        </div>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-auto px-4 pb-3">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 min-h-full">
          {/* LEFT column */}
          <div className="flex flex-col gap-3 min-h-0">
            {/* DepthGlass area is locked by parent to avoid reflow */}
            <div className="flex-1 min-h-[340px] max-h-[480px] overflow-hidden rounded-xl border border-zinc-700/60">
              <DepthGlass
                bid={qq?.bid}
                ask={qq?.ask}
                bids={qq?.bids}
                asks={qq?.asks}
                positionPrice={pos?.avg ?? pos?.avg_price ?? null}
                tape={tape}
                className="h-full w-full"
                onPickPrice={(p) => setSelectedPrice(p)}
              />
            </div>
          </div>

          {/* RIGHT column */}
          <div className="flex flex-col gap-3 min-h-0">
            <QuickTrade
              symbol={sym}
              bid={qq?.bid}
              ask={qq?.ask}
              enqueue={enqueue}
              selectedPrice={selectedPrice ?? undefined}
              onClearSelected={() => setSelectedPrice(null)}
            />
            <PositionPanel
              qty={pos?.qty ?? 0}
              avg={pos?.avg ?? pos?.avg_price ?? 0}
              uPnL={pos?.upnl ?? pos?.unrealized_pnl ?? 0}
              rPnL={pos?.rpnl ?? pos?.realized_pnl ?? 0}
            />
            <QuotePanel
              bid={qq?.bid}
              ask={qq?.ask}
              spreadBps={qq?.spread_bps}
              onPickPrice={(p) => setSelectedPrice(p)}
            />
          </div>

          {/* Orders / Fills */}
          <div className="md:col-span-2 rounded-xl border border-zinc-700/60 p-2">
            <OrdersFills symbol={sym} limit={10} />
          </div>
        </div>
      </div>

      {/* Footer metrics (sticky inside the card) */}
      <div
        className="
          sticky bottom-0 z-10
          border-t border-zinc-800/60
          bg-zinc-900/80 supports-[backdrop-filter]:bg-zinc-900/60 backdrop-blur
          px-4 py-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-sm
        "
      >
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
