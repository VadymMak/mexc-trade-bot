import React from "react";
import type { TradeStats } from "@/types";
import { formatNumber } from "@/utils/format";

type Props = {
  stats: TradeStats | null;
  loading?: boolean;
};

const TradeStatsSummary: React.FC<Props> = ({ stats, loading = false }) => {
  // ✅ КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: НЕ ЗАМЕНЯЕМ компонент при loading
  // Просто показываем skeleton вместо данных
  
  const cards = stats ? [
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
  ] : [];

  return (
    <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-zinc-100">
          Performance Summary
          {stats && (
            <span className="text-sm font-normal text-zinc-400 ml-2">
              ({stats.period})
            </span>
          )}
        </h2>
        
        {/* ✅ Индикатор обновления */}
        {loading && (
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse"></div>
            <span className="text-xs text-zinc-400">Updating...</span>
          </div>
        )}
      </div>
     
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {!stats ? (
          // ✅ Skeleton при первой загрузке
          Array.from({ length: 8 }).map((_, idx) => (
            <div key={idx} className="flex flex-col gap-1">
              <div className="h-3 w-20 bg-zinc-800 rounded animate-pulse"></div>
              <div className="h-6 w-16 bg-zinc-800 rounded animate-pulse"></div>
            </div>
          ))
        ) : (
          // ✅ Данные (с плавной анимацией при обновлении)
          cards.map((card, idx) => (
            <div 
              key={idx} 
              className={`flex flex-col gap-1 transition-opacity duration-200 ${
                loading ? 'opacity-60' : 'opacity-100'
              }`}
            >
              <span className="text-xs text-zinc-400">{card.label}</span>
              <span className={`text-xl font-semibold ${card.color}`}>
                {card.value}
              </span>
            </div>
          ))
        )}
      </div>

      {/* Net Profit with costs */}
      {stats?.net_profit !== undefined && (
        <div className={`mt-4 pt-4 border-t border-zinc-800 transition-opacity duration-200 ${
          loading ? 'opacity-60' : 'opacity-100'
        }`}>
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