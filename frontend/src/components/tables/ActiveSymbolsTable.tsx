import React from "react";
import { type Position } from "@/types/index";
import { useMarket } from "@/store/market";
import { useStrategy } from "@/store/strategy";
import { usePositionsStore } from "@/store/positions";
import { useToast } from "@/hooks/useToast";
import { getErrorMessage } from "@/lib/errors";
import { formatNumber } from "@/utils/format";

export type ActiveRowData = {
  qty: number;
  avg: number;
  mark: number;
  upnl: number;
  rpnlToday: number;
};

type Props = {
  symbols?: string[];
  onRowClick?: (symbol: string) => void;
  showDemoWhenEmpty?: boolean;
};

const DEMO: string[] = ["BANUSDT", "FETUSDT"];

function safeNum(v: unknown, def = 0): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : def;
  }
  return def;
}

const ActionButton: React.FC<
  React.PropsWithChildren<{
    onClick: (e: React.MouseEvent<HTMLButtonElement>) => void;
    title?: string;
    kind?: "primary" | "neutral" | "danger";
    disabled?: boolean;
  }>
> = ({ onClick, title, kind = "neutral", disabled, children }) => {
  const base =
    "inline-flex items-center justify-center px-3 py-1.5 rounded-lg text-xs font-medium leading-none transition-colors focus:outline-none focus-visible:ring-2 whitespace-nowrap shadow-sm";
  const theme =
    kind === "primary"
      ? "bg-emerald-600 hover:bg-emerald-500 text-white border border-emerald-400/40 focus-visible:ring-emerald-400"
      : kind === "danger"
      ? "bg-rose-600 hover:bg-rose-500 text-white border border-rose-400/40 focus-visible:ring-rose-400"
      : "bg-zinc-700 hover:bg-zinc-600 text-zinc-100 border border-zinc-600 focus-visible:ring-zinc-400";
  return (
    <button
      type="button"
      className={`${base} ${theme} ${disabled ? "opacity-60 cursor-not-allowed" : ""}`}
      onClick={onClick}
      title={title}
      disabled={disabled}
    >
      {children}
    </button>
  );
};

const ActiveSymbolsTable: React.FC<Props> = ({
  symbols = [],
  onRowClick,
  showDemoWhenEmpty = true,
}) => {
  const toast = useToast();

  // stores
  const positionsBySymbol = usePositionsStore((s) => s.positionsBySymbol);
  const quotesTick = useMarket((s) => s.quotesTick);
  const quoteOf = useMarket((s) => s.quoteOf);

  // strategy actions
  const start = useStrategy((s) => s.start);
  const stop = useStrategy((s) => s.stop);
  const busy = useStrategy((s) => s.busy);


  const rows: string[] = symbols.length === 0 && showDemoWhenEmpty ? DEMO : symbols;

  const getMarkPrice = React.useCallback(
    (symbol: string): number | undefined => {
      void quotesTick; // recompute when quotes change
      const q = quoteOf(symbol);
      if (!q) return undefined;
      if (q.mid && q.mid > 0) return q.mid;
      if (q.bid && q.ask && q.bid > 0 && q.ask > 0) return (q.bid + q.ask) / 2;
      return q.bid && q.bid > 0 ? q.bid : q.ask && q.ask > 0 ? q.ask : undefined;
    },
    [quoteOf, quotesTick]
  );

  const buildRow = (symbol: string): ActiveRowData => {
    const SYM = (symbol || "").toUpperCase();
    const p: Position | undefined = (positionsBySymbol as Record<string, Position | undefined>)[SYM];

    const qty = safeNum(p?.qty, 0);
    const avg = safeNum(p?.avg_price ?? p?.avg, 0);
    const mark = safeNum(getMarkPrice(SYM), 0);

    const upnl =
      mark && avg ? (mark - avg) * qty : safeNum(p?.unrealized_pnl ?? p?.upnl, 0);

    const rpnlToday = safeNum(p?.realized_pnl ?? p?.rpnl, 0);

    return { qty, avg, mark, upnl, rpnlToday };
  };

  const onStart = async (symbol: string) => {
    try {
      await start([symbol]);
      toast.success(`${symbol} started`);
    } catch (e) {
      toast.error(getErrorMessage(e) || "Start failed", "Start");
    }
  };

  const onStop = async (symbol: string) => {
    try {
      await stop([symbol], false);
      toast.info(`${symbol} stopped (no flatten)`);
    } catch (e) {
      toast.error(getErrorMessage(e) || "Stop failed", "Stop");
    }
  };

  const onFlatten = async (symbol: string) => {
    try {
      await stop([symbol], true);
      toast.info(`${symbol} flattened`);
    } catch (e) {
      toast.error(getErrorMessage(e) || "Flatten failed", "Flatten");
    }
  };

  const onSettings = (symbol: string) => {
    window.dispatchEvent(new CustomEvent("open-strategy-settings", { detail: { symbol } }));
  };

  return (
    <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-zinc-800/50 text-zinc-400">
          <tr>
            <th className="px-4 py-2 text-left">Symbol</th>
            <th className="px-4 py-2 text-right">Qty</th>
            <th className="px-4 py-2 text-right">Avg</th>
            <th className="px-4 py-2 text-right">Mark</th>
            <th className="px-4 py-2 text-right">uPnL</th>
            <th className="px-4 py-2 text-right">rPnL (Today)</th>
            <th className="px-4 py-2 text-right">Exposure</th>
            <th className="px-4 py-2 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {rows.length === 0 ? (
            <tr>
              <td colSpan={8} className="px-4 py-6 text-center text-zinc-500 italic">
                Нет активных символов
              </td>
            </tr>
          ) : (
            rows.map((symbol) => {
              const data = buildRow(symbol);
              const exposure = data.mark * data.qty;

              return (
                <tr
                  key={symbol}
                  className="hover:bg-zinc-800/40 cursor-pointer"
                  onClick={() => onRowClick?.(symbol)}
                >
                  <td className="px-4 py-2 font-medium text-zinc-200">{symbol}</td>
                  <td className="px-4 py-2 text-right">{formatNumber(data.qty, 4)}</td>
                  <td className="px-4 py-2 text-right">{formatNumber(data.avg, 6)}</td>
                  <td className="px-4 py-2 text-right">{formatNumber(data.mark, 6)}</td>
                  <td className="px-4 py-2 text-right">
                    <span className={data.upnl >= 0 ? "text-emerald-400" : "text-rose-400"}>
                      {formatNumber(data.upnl, 2)}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    <span className={data.rpnlToday >= 0 ? "text-emerald-400" : "text-rose-400"}>
                      {formatNumber(data.rpnlToday, 2)}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">{formatNumber(exposure, 2)}</td>

                  <td className="px-4 py-2 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                    <div className="flex justify-end gap-2">
                      {data.qty > 0 ? (
                        <>
                          <ActionButton title="Stop strategy (keep position open)" onClick={() => onStop(symbol)} disabled={busy}>
                            Stop
                          </ActionButton>
                          <ActionButton
                            title="Close position immediately"
                            kind="danger"
                            onClick={() => onFlatten(symbol)}
                            disabled={busy}
                          >
                            Flatten
                          </ActionButton>
                        </>
                      ) : (
                        <ActionButton
                          title="Start trading this symbol"
                          kind="primary"
                          onClick={() => onStart(symbol)}
                          disabled={busy}
                        >
                          Start
                        </ActionButton>
                      )}
                      
                      <ActionButton title="Symbol settings" onClick={() => onSettings(symbol)}>
                        ⚙
                      </ActionButton>
                    </div>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
};

export default ActiveSymbolsTable;
