import React from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import type { Trade } from "@/types";

type Props = {
  trades: Trade[];
  period: string;
};

type DataPoint = {
  time: string;
  fullTime: string;
  cumulative: number;
  trade_count: number;
};

const CumulativePnLChart: React.FC<Props> = ({ trades, period }) => {
  // Calculate cumulative P&L data
  const data: DataPoint[] = React.useMemo(() => {
    // Sort trades by entry time
    const sorted = [...trades]
      .filter(t => t.status === "CLOSED" && t.entry_time)
      .sort((a, b) => new Date(a.entry_time).getTime() - new Date(b.entry_time).getTime());

    let cumulative = 0;
    const points: DataPoint[] = [];

    // Add starting point at $0
    if (sorted.length > 0) {
      const firstDate = new Date(sorted[0].entry_time);
      const firstTime = firstDate.toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
      });
      points.push({
        time: firstTime,
        fullTime: firstDate.toLocaleTimeString("ru-RU"),
        cumulative: 0,
        trade_count: 0,
      });
    }

    sorted.forEach((trade, idx) => {
      cumulative += trade.pnl_usd || 0;
      
      const date = new Date(trade.entry_time);
      const timeStr = date.toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
      });

      points.push({
        time: timeStr,
        fullTime: date.toLocaleTimeString("ru-RU"),
        cumulative: Number(cumulative.toFixed(2)),
        trade_count: idx + 1,
      });
    });

    return points;
  }, [trades]);

  // Determine line color based on final P&L
  const finalPnL = data.length > 0 ? data[data.length - 1].cumulative : 0;
  const lineColor = finalPnL >= 0 ? "#10b981" : "#ef4444"; // emerald-500 : rose-500
  const gradientId = finalPnL >= 0 ? "colorGreen" : "colorRed";

  if (data.length === 0) {
    return (
      <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 p-8 text-center">
        <p className="text-zinc-400">Нет данных для графика</p>
      </div>
    );
  }

  // Custom tooltip
  const CustomTooltip = ({ active, payload }: { 
    active?: boolean; 
    payload?: Array<{ payload: DataPoint }> 
  }) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div className="bg-zinc-900/95 border border-zinc-700 rounded-xl p-4 shadow-2xl backdrop-blur">
          <p className="text-xs text-zinc-400 mb-1">{data.fullTime}</p>
          <p className={`text-lg font-bold ${
            data.cumulative >= 0 ? "text-emerald-400" : "text-rose-400"
          }`}>
            {data.cumulative >= 0 ? "+" : ""}${data.cumulative.toFixed(2)}
          </p>
          <p className="text-xs text-zinc-500 mt-1">
            {data.trade_count} {data.trade_count === 1 ? "trade" : "trades"}
          </p>
        </div>
      );
    }
    return null;
  };

  // Sample every Nth point for cleaner X-axis
  const sampleInterval = Math.max(1, Math.floor(data.length / 12));
  const sampledTicks = data
    .filter((_, idx) => idx % sampleInterval === 0)
    .map(d => d.time);

  return (
    <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/40 p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">
            Cumulative P&L
          </h2>
          <p className="text-sm text-zinc-400">{period} • {data[data.length - 1]?.trade_count || 0} trades</p>
        </div>
        <div className="text-right">
          <div className={`text-2xl font-bold ${
            finalPnL >= 0 ? "text-emerald-400" : "text-rose-400"
          }`}>
            {finalPnL >= 0 ? "+" : ""}${finalPnL.toFixed(2)}
          </div>
          <p className="text-xs text-zinc-500">Final P&L</p>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="colorGreen" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
              <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
            </linearGradient>
            <linearGradient id="colorRed" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
              <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
            </linearGradient>
          </defs>
          <CartesianGrid 
            strokeDasharray="3 3" 
            stroke="#27272a" 
            vertical={false}
          />
          <XAxis 
            dataKey="time" 
            stroke="#52525b"
            style={{ fontSize: "11px" }}
            tickLine={false}
            ticks={sampledTicks}
          />
          <YAxis 
            stroke="#52525b"
            style={{ fontSize: "11px" }}
            tickLine={false}
            tickFormatter={(value) => `$${value}`}
            width={60}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ stroke: "#52525b", strokeWidth: 1 }} />
          <Line
            type="monotone"
            dataKey="cumulative"
            stroke={lineColor}
            strokeWidth={3}
            dot={false}
            fill={`url(#${gradientId})`}
            activeDot={{ r: 6, fill: lineColor, strokeWidth: 2, stroke: "#18181b" }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

export default CumulativePnLChart;