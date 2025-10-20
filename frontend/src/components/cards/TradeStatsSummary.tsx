import React from "react";
import type { TradeStats } from "@/types";
import { formatNumber } from "@/utils/format";

type Props = {
  stats: TradeStats | null;
  loading?: boolean;
};

const TradeStatsSummary: React.FC<Props> = ({ stats, loading = false }) => {
  if (loading || !stats) {
    return (
      <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 p-6">
        <h2 className="text-lg font-semibold text-zinc-100 mb-4">Performance Summary</h2>
        <p className="text-zinc-400">Загрузка статистики...</p>
      </div>
    );
  }

  const cards = [
    {
      label: "Total Trades",
      value: stats.total_trades.toString(),
      color: "text-zinc-100",
    },
    {
      label: "Win Rate",
      value: `${stats.win_rate.toFixed(1)}%`,
      color: stats.win_rate >= 50 ? "text-emerald-400" : "text-rose-400",
    },
    {
      label: "Gross P&L",
      value: `$${formatNumber(stats.gross_profit, 2)}`,
      color: stats.gross_profit >= 0 ? "text-emerald-400" : "text-rose-400",
    },
    {
      label: "Trading Fees",
      value: `$${formatNumber(stats.trading_fees, 2)}`,
      color: "text-amber-400",
    },
    {
      label: "Avg P&L/Trade",
      value: `$${formatNumber(stats.avg_profit_per_trade, 4)}`,
      color: stats.avg_profit_per_trade >= 0 ? "text-emerald-400" : "text-rose-400",
    },
    {
      label: "Avg Duration",
      value: `${stats.avg_duration_sec.toFixed(1)}s`,
      color: "text-zinc-100",
    },
    {
      label: "Best Trade",
      value: `$${formatNumber(stats.best_trade, 4)}`,
      color: "text-emerald-400",
    },
    {
      label: "Worst Trade",
      value: `$${formatNumber(stats.worst_trade, 4)}`,
      color: "text-rose-400",
    },
  ];

  return (
    <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 p-6">
      <h2 className="text-lg font-semibold text-zinc-100 mb-4">
        Performance Summary
        <span className="text-sm font-normal text-zinc-400 ml-2">
          ({stats.period})
        </span>
      </h2>
      
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {cards.map((card, idx) => (
          <div key={idx} className="flex flex-col gap-1">
            <span className="text-xs text-zinc-400">{card.label}</span>
            <span className={`text-xl font-semibold ${card.color}`}>
              {card.value}
            </span>
          </div>
        ))}
      </div>

      {/* Net Profit with costs */}
      {stats.net_profit !== undefined && (
        <div className="mt-4 pt-4 border-t border-zinc-800">
          <div className="flex items-center justify-between">
            <span className="text-sm text-zinc-400">
              Net Profit (after infrastructure costs)
            </span>
            <span className={`text-lg font-semibold ${
              stats.net_profit >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}>
              ${formatNumber(stats.net_profit, 2)}
            </span>
          </div>
          {stats.infrastructure_costs !== undefined && (
            <div className="flex items-center justify-between mt-2">
              <span className="text-xs text-zinc-500">
                Infrastructure costs
              </span>
              <span className="text-xs text-zinc-400">
                ${formatNumber(stats.infrastructure_costs, 2)}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default TradeStatsSummary;