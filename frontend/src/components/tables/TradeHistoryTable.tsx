import React from "react";
import type { Trade } from "@/types";
import { formatNumber } from "@/utils/format";

type SortField = "entry_time" | "symbol" | "pnl_usd" | "pnl_percent" | "hold_duration_sec";
type SortOrder = "asc" | "desc";

type Props = {
  trades: Trade[];
  loading?: boolean;
  sortField?: SortField;
  sortOrder?: SortOrder;
  onSort?: (field: SortField) => void;
};

const TradeHistoryTable: React.FC<Props> = ({ 
  trades, 
  loading = false,
  sortField,
  sortOrder,
  onSort
}) => {
  const formatDateTime = (isoString: string | null | undefined): string => {
    if (!isoString) return "—";
    const date = new Date(isoString);
    return date.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  const formatDuration = (seconds: number | null | undefined): string => {
    if (!seconds) return "—";
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const minutes = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${minutes}m ${secs}s`;
  };

  // ✅ Sort indicator
  const SortIcon: React.FC<{ field: SortField }> = ({ field }) => {
    if (sortField !== field) {
      return <span className="text-zinc-600 ml-1">⇅</span>;
    }
    return (
      <span className="text-indigo-400 ml-1">
        {sortOrder === "asc" ? "↑" : "↓"}
      </span>
    );
  };

  // ✅ Sortable header
  const SortableHeader: React.FC<{ field: SortField; children: React.ReactNode; align?: string }> = ({ 
    field, 
    children,
    align = "text-left"
  }) => {
    return (
      <th 
        className={`px-4 py-2 ${align} cursor-pointer hover:bg-zinc-700/50 transition-colors select-none`}
        onClick={() => onSort?.(field)}
      >
        <div className="flex items-center justify-start">
          {children}
          <SortIcon field={field} />
        </div>
      </th>
    );
  };

  if (loading) {
    return (
      <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 p-8 text-center">
        <p className="text-zinc-400">Загрузка трейдов...</p>
      </div>
    );
  }

  if (trades.length === 0) {
    return (
      <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 p-8 text-center">
        <p className="text-zinc-400">Нет трейдов за выбранный период</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-zinc-800/50 text-zinc-400">
            <tr>
              <SortableHeader field="entry_time">Entry Time</SortableHeader>
              <SortableHeader field="symbol">Symbol</SortableHeader>
              <th className="px-4 py-2 text-left">Side</th>
              <th className="px-4 py-2 text-right">Entry Price</th>
              <th className="px-4 py-2 text-right">Exit Price</th>
              <th className="px-4 py-2 text-right">Qty</th>
              <SortableHeader field="pnl_usd" align="text-right">P&L USD</SortableHeader>
              <SortableHeader field="pnl_percent" align="text-right">P&L %</SortableHeader>
              <th className="px-4 py-2 text-right">Fee</th>
              <SortableHeader field="hold_duration_sec" align="text-right">Duration</SortableHeader>
              <th className="px-4 py-2 text-center">Exit</th>
              <th className="px-4 py-2 text-center">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {trades.map((trade) => {
              const pnlColor = trade.pnl_usd > 0 
                ? "text-emerald-400" 
                : trade.pnl_usd < 0 
                ? "text-rose-400" 
                : "text-zinc-400";

              const statusColor = trade.status === "CLOSED" 
                ? "text-zinc-400" 
                : "text-amber-400";

              return (
                <tr key={trade.id} className="hover:bg-zinc-800/40">
                  <td className="px-4 py-2 text-zinc-300 text-xs">
                    {formatDateTime(trade.entry_time)}
                  </td>
                  <td className="px-4 py-2 font-medium text-zinc-200">
                    {trade.symbol}
                  </td>
                  <td className="px-4 py-2 text-zinc-300">
                    {trade.entry_side}
                  </td>
                  <td className="px-4 py-2 text-right text-zinc-300">
                    {formatNumber(trade.entry_price, 8)}
                  </td>
                  <td className="px-4 py-2 text-right text-zinc-300">
                    {trade.exit_price ? formatNumber(trade.exit_price, 8) : "—"}
                  </td>
                  <td className="px-4 py-2 text-right text-zinc-300">
                    {formatNumber(trade.entry_qty, 4)}
                  </td>
                  <td className={`px-4 py-2 text-right font-medium ${pnlColor}`}>
                    {formatNumber(trade.pnl_usd, 4)}
                  </td>
                  <td className={`px-4 py-2 text-right ${pnlColor}`}>
                    {formatNumber(trade.pnl_percent, 2)}%
                  </td>
                  <td className="px-4 py-2 text-right text-zinc-400">
                    {formatNumber(trade.total_fee, 4)}
                  </td>
                  <td className="px-4 py-2 text-right text-zinc-300">
                    {formatDuration(trade.hold_duration_sec)}
                  </td>
                  <td className="px-4 py-2 text-center">
                    {trade.exit_reason ? (
                      <span className={`px-2 py-1 rounded text-xs ${
                        trade.exit_reason === "TP" 
                          ? "bg-emerald-900/40 text-emerald-400" 
                          : trade.exit_reason === "SL" 
                          ? "bg-rose-900/40 text-rose-400" 
                          : "bg-amber-900/40 text-amber-400"
                      }`}>
                        {trade.exit_reason}
                      </span>
                    ) : (
                      <span className="text-zinc-500">—</span>
                    )}
                  </td>
                  <td className={`px-4 py-2 text-center text-xs ${statusColor}`}>
                    {trade.status}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default TradeHistoryTable;