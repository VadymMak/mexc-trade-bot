import React, { useState, useEffect } from "react";
import PageToolbar from "@/components/layout/PageToolbar";
import Toaster from "@/components/common/Toaster";
import TradeHistoryTable from "@/components/tables/TradeHistoryTable";
import TradeStatsSummary from "@/components/cards/TradeStatsSummary";
import CumulativePnLChart from "@/components/charts/CumulativePnLChart";
import { fetchTrades, fetchTradeStats, exportTradesCSV } from "@/api/trades";
import type { Trade, TradeStats } from "@/types";

type SortField = "entry_time" | "symbol" | "pnl_usd" | "pnl_percent" | "hold_duration_sec";
type SortOrder = "asc" | "desc";

const TradeLog: React.FC = () => {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [loading, setLoading] = useState(true);
  
  // Filters
  const [period, setPeriod] = useState<string>("today");
  const [symbol, setSymbol] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>(""); // ✅ NEW: Search
  
  // Sorting
  const [sortField, setSortField] = useState<SortField>("entry_time"); // ✅ NEW: Sort field
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc"); // ✅ NEW: Sort order
  
  // Pagination
  const [page, setPage] = useState(1);
  const itemsPerPage = 50;

  // Auto-refresh
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [lastUpdated, setLastUpdated] = useState<Date>(new Date());

  // Extract unique symbols from trades
  const uniqueSymbols = React.useMemo(() => {
    const symbols = new Set(trades.map(t => t.symbol));
    return Array.from(symbols).sort();
  }, [trades]);

  // Load data
  const loadData = React.useCallback(async () => {
    setLoading(true);
    try {
        const [tradesData, statsData] = await Promise.all([
        fetchTrades({ 
            period, 
            symbol: symbol || undefined,
            status: status || undefined,
            limit: 500
        }),
        fetchTradeStats({ period, include_costs: true }),
        ]);
        setTrades(tradesData);
        setStats(statsData);
        setPage(1);
        setLastUpdated(new Date()); // ✅ UPDATE timestamp
    } catch (err) {
        console.error("Failed to load trade data:", err);
    } finally {
        setLoading(false);
    }
    }, [period, symbol, status]);

    // Load data on filter change
    useEffect(() => {
    loadData();
    }, [loadData]);

    // Auto-refresh interval
useEffect(() => {
  if (!autoRefresh) return;

  const interval = setInterval(() => {
    loadData();
  }, 10000); // 10 seconds

  return () => clearInterval(interval);
}, [autoRefresh, loadData]);

  // ✅ NEW: Filtered trades (by search)
  const filteredTrades = React.useMemo(() => {
    if (!searchQuery.trim()) return trades;
    const query = searchQuery.toLowerCase();
    return trades.filter(t => 
      t.symbol.toLowerCase().includes(query) ||
      t.trade_id.toLowerCase().includes(query)
    );
  }, [trades, searchQuery]);

  // ✅ NEW: Sorted trades
  const sortedTrades = React.useMemo(() => {
    const sorted = [...filteredTrades];
    sorted.sort((a, b) => {
        let aVal: string | number | null | undefined = a[sortField];
        let bVal: string | number | null | undefined = b[sortField];

      // Handle null/undefined
      if (aVal == null) aVal = sortOrder === "asc" ? Infinity : -Infinity;
      if (bVal == null) bVal = sortOrder === "asc" ? Infinity : -Infinity;

      // Date comparison
      if (sortField === "entry_time") {
        aVal = new Date(aVal).getTime();
        bVal = new Date(bVal).getTime();
      }

      if (sortOrder === "asc") {
        return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
      } else {
        return aVal < bVal ? 1 : aVal > bVal ? -1 : 0;
      }
    });
    return sorted;
  }, [filteredTrades, sortField, sortOrder]);

  // Paginated trades
  const paginatedTrades = React.useMemo(() => {
    const start = (page - 1) * itemsPerPage;
    const end = start + itemsPerPage;
    return sortedTrades.slice(start, end);
  }, [sortedTrades, page]);

  const totalPages = Math.ceil(sortedTrades.length / itemsPerPage);

  const handleExport = () => {
    exportTradesCSV({ 
      period,
      symbol: symbol || undefined,
      status: status || undefined
    });
  };

  const handlePrevPage = () => {
    if (page > 1) setPage(page - 1);
  };

  const handleNextPage = () => {
    if (page < totalPages) setPage(page + 1);
  };

  // ✅ NEW: Toggle sort
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder(sortOrder === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortOrder("desc");
    }
  };

  // ✅ Manual refresh
const handleRefresh = () => {
  loadData();
};


  return (
    <div className="flex flex-col gap-6 p-4 md:p-6 lg:p-8">
      <Toaster />
      
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl md:text-2xl font-semibold text-zinc-100">Trade Log</h1>
          <p className="text-zinc-400 text-sm">
            Полная история сделок с фильтрацией и анализом производительности.
          </p>
        </div>
        <PageToolbar />
      </header>

      {/* Filters & Export */}
      <section className="flex flex-wrap items-center gap-4">
        {/* Period Filter */}
        <div className="flex items-center gap-2">
          <label className="text-sm text-zinc-400">Period:</label>
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            className="px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-100 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="today">Today</option>
            <option value="wtd">This Week</option>
            <option value="mtd">This Month</option>
            <option value="all">All Time</option>
          </select>
        </div>

        {/* Symbol Filter */}
        <div className="flex items-center gap-2">
          <label className="text-sm text-zinc-400">Symbol:</label>
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-100 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">All Symbols</option>
            {uniqueSymbols.map((sym) => (
              <option key={sym} value={sym}>{sym}</option>
            ))}
          </select>
        </div>

        {/* Status Filter */}
        <div className="flex items-center gap-2">
          <label className="text-sm text-zinc-400">Status:</label>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-100 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">All Status</option>
            <option value="OPEN">Open</option>
            <option value="CLOSED">Closed</option>
          </select>
        </div>

        {/* ✅ NEW: Search Input */}
        <div className="flex items-center gap-2">
          <label className="text-sm text-zinc-400">Search:</label>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Symbol or Trade ID..."
            className="px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-100 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 w-48"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="text-zinc-400 hover:text-zinc-200 text-xs"
            >
              ✕
            </button>
          )}
        </div>

        {/* Export Button */}
        {/* Refresh Button */}
        <button
        onClick={handleRefresh}
        disabled={loading}
        className="ml-auto px-4 py-2 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-white text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-zinc-500 disabled:opacity-50"
        >
        🔄 Refresh
        </button>

        {/* Export Button */}
        <button
        onClick={handleExport}
        className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
        📥 Export CSV
        </button>
      </section>

      {/* Auto-refresh controls */}
            <section className="flex items-center justify-between px-4 py-3 rounded-lg bg-zinc-800/40 border border-zinc-700/50">
            <div className="flex items-center gap-3">
                <span className="text-sm text-zinc-400">Auto-refresh:</span>
                <button
                onClick={() => setAutoRefresh(!autoRefresh)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    autoRefresh
                    ? "bg-emerald-600 hover:bg-emerald-500 text-white"
                    : "bg-zinc-700 hover:bg-zinc-600 text-zinc-300"
                }`}
                >
                {autoRefresh ? "ON" : "OFF"}
                </button>
                <span className="text-xs text-zinc-500">
                {autoRefresh ? "(every 10s)" : "(manual only)"}
                </span>
            </div>

            <LastUpdatedIndicator lastUpdated={lastUpdated} />
            </section>

      {/* Performance Summary */}
      <section>
        <TradeStatsSummary stats={stats} loading={loading} />
      </section>
      <section>
            <CumulativePnLChart trades={trades} period={period} />
        </section>

      {/* Trade History Table */}
      <main className="flex-1">
        <TradeHistoryTable 
          trades={paginatedTrades} 
          loading={loading}
          sortField={sortField}
          sortOrder={sortOrder}
          onSort={handleSort}
        />
      </main>

      {/* Pagination */}
      {!loading && totalPages > 1 && (
        <section className="flex items-center justify-between">
          <button
            onClick={handlePrevPage}
            disabled={page === 1}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              page === 1
                ? "bg-zinc-800 text-zinc-500 cursor-not-allowed"
                : "bg-zinc-800 hover:bg-zinc-700 text-zinc-100"
            }`}
          >
            ← Previous
          </button>

          <span className="text-sm text-zinc-400">
            Page {page} of {totalPages} ({sortedTrades.length} total trades)
          </span>

          <button
            onClick={handleNextPage}
            disabled={page === totalPages}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              page === totalPages
                ? "bg-zinc-800 text-zinc-500 cursor-not-allowed"
                : "bg-zinc-800 hover:bg-zinc-700 text-zinc-100"
            }`}
          >
            Next →
          </button>
        </section>
      )}
    </div>
  );
};

// ✅ Live updating indicator component
const LastUpdatedIndicator: React.FC<{ lastUpdated: Date }> = ({ lastUpdated }) => {
  const [, setTick] = useState(0);

  // Force re-render every second to update "ago" text
  useEffect(() => {
    const interval = setInterval(() => {
      setTick(t => t + 1);
    }, 1000);

    return () => clearInterval(interval);
  }, []);

  const formatLastUpdated = () => {
    const now = new Date();
    const diff = Math.floor((now.getTime() - lastUpdated.getTime()) / 1000);
    if (diff < 10) return "just now";
    if (diff < 60) return `${diff}s ago`;
    const minutes = Math.floor(diff / 60);
    return `${minutes}m ago`;
  };

  return (
    <div className="text-sm text-zinc-400">
      Last updated: <span className="text-zinc-300">{formatLastUpdated()}</span>
    </div>
  );
};

export default TradeLog;