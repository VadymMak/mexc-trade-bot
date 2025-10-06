import React, { useMemo } from "react";
import ActiveSymbolsTable from "@/components/tables/ActiveSymbolsTable";
import PositionSummary from "@/components/cards/PositionSummary";
import PageToolbar from "@/components/layout/PageToolbar";
import Toaster from "@/components/common/Toaster";
import { usePositionsStore } from "@/store/positions";

const DEMO_SYMBOLS: string[] = ["BANUSDT", "FETUSDT"];

const TradingBoard: React.FC = () => {
  // symbols from positions (fallback to demo)
  const positionsBySymbol = usePositionsStore((s) => s.positionsBySymbol);
  const symbols: string[] = useMemo(() => {
    const keys = Object.keys(positionsBySymbol);
    return keys.length > 0 ? keys : DEMO_SYMBOLS;
  }, [positionsBySymbol]);

  const handleRowClick = (symbol: string): void => {
    // open details / modal if needed
    console.log("Open details for:", symbol);
  };

  return (
    <div className="flex flex-col gap-6 p-4 md:p-6 lg:p-8">
      <Toaster />

      {/* Header */}
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl md:text-2xl font-semibold text-zinc-100">Trading Board</h1>
          <p className="text-zinc-400 text-sm">
            Управление активными тикерами, быстрые действия и независимые настройки по каждому символу.
          </p>
        </div>
        <PageToolbar />
      </header>

      {/* Summary */}
      <section>
        <PositionSummary />
      </section>

      {/* Table */}
      <main className="flex-1">
        <ActiveSymbolsTable symbols={symbols} onRowClick={handleRowClick} />
      </main>
    </div>
  );
};

export default TradingBoard;
