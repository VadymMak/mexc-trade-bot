import React, { useMemo, useEffect, useState } from "react";
import ActiveSymbolsTable from "@/components/tables/ActiveSymbolsTable";
import PositionSummary from "@/components/cards/PositionSummary";
import PageToolbar from "@/components/layout/PageToolbar";
import Toaster from "@/components/common/Toaster";
import EmergencyStopDialog from "@/components/modals/EmergencyStopDialog";
import { usePositions } from "@/hooks/usePositions";
import { useProvider } from "@/store/provider";
import { useStrategyMetrics } from "@/store/strategyMetrics";
import { useToast } from "@/hooks/useToast";
import { getErrorMessage } from "@/lib/errors";
import { useSymbolItems } from "@/store/symbols";
import { useSymbols } from "@/store/symbols";
import { useStrategy } from "@/store/strategy";
import http from "@/lib/http";

const TradingBoard: React.FC = () => {
  const loadProvider = useProvider((s) => s.load);
  const loadMetrics = useStrategyMetrics((s) => s.loadMetrics); // ✅ ДОБАВЛЕНО

  const toast = useToast();
  
  // Emergency Stop Dialog state
  const [showEmergencyDialog, setShowEmergencyDialog] = useState(false);
  const [stoppingAll, setStoppingAll] = useState(false);
  const [symbolInput, setSymbolInput] = useState("");
  const addSymbol = useSymbols((s) => s.add);
  const startStrategy = useStrategy((s) => s.start);

  // Загрузить provider при монтировании
  useEffect(() => {
    loadProvider();
  }, [loadProvider]);

  // ✅ ДОБАВЛЕНО: Автообновление метрик стратегии каждые 3 секунды
  // Auto-refresh metrics every 15 seconds
  useEffect(() => {
    loadMetrics(); // Initial load
    const interval = setInterval(() => {
      loadMetrics();
    }, 15000);  // ← Every 15 seconds (was 3000)
    return () => clearInterval(interval);
  }, [loadMetrics]);

  const { positions } = usePositions({
    intervalMs: 10000,
    immediate: true,
    pauseWhenHidden: true
  });

  useEffect(() => {
    console.log("🔍 [TradingBoard] positions changed:", positions);
  }, [positions]);

  // ✅ Get symbols from useSymbols store instead of positions
  const symbolItems = useSymbolItems();
  const symbols: string[] = useMemo(() => {
  // Символы из позиций
  const positionSymbols = positions.map(p => p.symbol);
  
  // Символы из конфигурации
  const configuredSymbols = symbolItems.map(item => item.symbol);
  
  // Объедини и убери дубликаты
  return Array.from(new Set([...positionSymbols, ...configuredSymbols]));
}, [positions, symbolItems]);

  const handleRowClick = (symbol: string): void => {
    console.log("Open details for:", symbol);
  };

  const handleEmergencyStop = () => {
    setShowEmergencyDialog(true);
  };

  const handleConfirmStop = async () => {
  setShowEmergencyDialog(false);
  setStoppingAll(true);
  
  try {
    await http.post("/api/strategy/stop-all", { flatten: true });
    
    // ✅ Update symbols store (set all running: false)
    useSymbols.getState().stopAll();
    
    toast.success(
      "All strategies stopped and positions flattened",
      "🚨 Emergency Stop"
    );
    
    setTimeout(() => {
      loadMetrics();
      window.dispatchEvent(new Event('positions-force-reload'));
    }, 1000);
    
    setTimeout(() => {
      loadMetrics();
      window.dispatchEvent(new Event('positions-force-reload'));
    }, 3000);
    
    setTimeout(() => {
      loadMetrics();
      window.dispatchEvent(new Event('positions-force-reload'));
    }, 5000);
    
  } catch (error) {
    toast.error(
      getErrorMessage(error),
      "Emergency Stop Failed"
    );
  } finally {
    setStoppingAll(false);
  }
};

  const handleCancelStop = () => {
    setShowEmergencyDialog(false);
  };

  const handleAddSymbol = async () => {
  const symbol = symbolInput.trim().toUpperCase();
  if (!symbol) return;
  
  try {
    // Add to symbols store
    addSymbol(symbol);
    
    // Start strategy immediately
    await startStrategy([symbol]);
    
    toast.success(`${symbol} added and started`);
    setSymbolInput(""); // Clear input
    
    // Refresh after delay
    setTimeout(() => {
      loadMetrics();
    }, 1000);
  } catch (error) {
    toast.error(
      getErrorMessage(error),
      "Failed to add symbol"
    );
  }
};

const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
  if (e.key === "Enter") {
    handleAddSymbol();
  }
};

  return (
    <div className="flex flex-col gap-6 p-4 md:p-6 lg:p-8">
      <Toaster />
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl md:text-2xl font-semibold text-zinc-100">Trading Board</h1>
          <p className="text-zinc-400 text-sm">
            Управление активными тикерами, быстрые действия и независимые настройки по каждому символу.
          </p>
        </div>
        
        <div className="flex items-center gap-3">
          {/* Add Symbol Input */}
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
              onKeyPress={handleKeyPress}
              placeholder="BTCUSDT"
              className="px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-100 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 w-32"
            />
            <button
              onClick={handleAddSymbol}
              disabled={!symbolInput.trim()}
              className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-lg hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              <svg 
                className="h-4 w-4" 
                fill="none" 
                viewBox="0 0 24 24" 
                stroke="currentColor"
              >
                <path 
                  strokeLinecap="round" 
                  strokeLinejoin="round" 
                  strokeWidth={2} 
                  d="M12 4v16m8-8H4" 
                />
              </svg>
              Add
            </button>
          </div>
          
          {/* Emergency Stop Button */}
          <button
            onClick={handleEmergencyStop}
            disabled={stoppingAll}
            className="flex items-center gap-2 rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white shadow-lg hover:bg-rose-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:shadow-rose-500/50"
          >
            {stoppingAll ? (
              <>
                <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                Stopping...
              </>
            ) : (
              <>
                <svg 
                  className="h-4 w-4" 
                  fill="none" 
                  viewBox="0 0 24 24" 
                  stroke="currentColor"
                >
                  <path 
                    strokeLinecap="round" 
                    strokeLinejoin="round" 
                    strokeWidth={2} 
                    d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" 
                  />
                  <path 
                    strokeLinecap="round" 
                    strokeLinejoin="round" 
                    strokeWidth={2} 
                    d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" 
                  />
                </svg>
                EMERGENCY STOP
              </>
            )}
          </button>
          
          <PageToolbar />
        </div>
      </header>
      <section>
        <PositionSummary />
      </section>
      <main className="flex-1">
        <ActiveSymbolsTable symbols={symbols} onRowClick={handleRowClick} />
      </main>
      
      {/* Emergency Stop Dialog */}
      <EmergencyStopDialog
        open={showEmergencyDialog}
        positionsCount={positions.length}
        onConfirm={handleConfirmStop}
        onCancel={handleCancelStop}
      />
    </div>
  );
};

export default TradingBoard;