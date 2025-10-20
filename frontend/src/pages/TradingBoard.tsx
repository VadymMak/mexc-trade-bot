import React, { useMemo, useEffect } from "react";
import ActiveSymbolsTable from "@/components/tables/ActiveSymbolsTable";
import PositionSummary from "@/components/cards/PositionSummary";
import PageToolbar from "@/components/layout/PageToolbar";
import Toaster from "@/components/common/Toaster";
import { usePositions } from "@/hooks/usePositions";
import { useProvider } from "@/store/provider";
import { useStrategyMetrics } from "@/store/strategyMetrics"; // ✅ ДОБАВЛЕНО

const TradingBoard: React.FC = () => {
  const loadProvider = useProvider((s) => s.load);
  const loadMetrics = useStrategyMetrics((s) => s.loadMetrics); // ✅ ДОБАВЛЕНО

  // Загрузить provider при монтировании
  useEffect(() => {
    loadProvider();
  }, [loadProvider]);

  // ✅ ДОБАВЛЕНО: Автообновление метрик стратегии каждые 3 секунды
  useEffect(() => {
    loadMetrics(); // Первая загрузка

    const interval = setInterval(() => {
      loadMetrics();
    }, 3000);

    return () => clearInterval(interval);
  }, [loadMetrics]);

  const { positions } = usePositions({
    intervalMs: 3000,
    immediate: true,
    pauseWhenHidden: true
  });

  useEffect(() => {
    console.log("🔍 [TradingBoard] positions changed:", positions);
  }, [positions]);

  const symbols: string[] = useMemo(() => {
    const syms = positions.map(p => p.symbol);
    console.log("🔍 [TradingBoard] symbols:", syms);
    return syms;
  }, [positions]);

  const handleRowClick = (symbol: string): void => {
    console.log("Open details for:", symbol);
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
        <PageToolbar />
      </header>
      <section>
        <PositionSummary />
      </section>
      <main className="flex-1">
        <ActiveSymbolsTable symbols={symbols} onRowClick={handleRowClick} />
      </main>
    </div>
  );
};

export default TradingBoard;