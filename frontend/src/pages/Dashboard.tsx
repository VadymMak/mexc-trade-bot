// src/routes/Dashboard.tsx
import { useEffect, useMemo, useRef } from "react";
import TopBar from "@/components/layout/TopBar";
import SymbolCard from "@/components/cards/SymbolCard";
import { useSymbols } from "@/store/symbols";
import { useMarket } from "@/store/market";
import { useMetrics } from "@/store/metrics";
import { apiGetMetrics, apiGetPositions, apiGetUISnapshot } from "@/api/api";
import { useInterval } from "@/hooks/useInterval";
import { useSSEQuotes } from "@/hooks/useSSEQuotes";
import { useOrders } from "@/store/orders";
import type { UISnapshot } from "@/types/api";

export default function Dashboard() {
  const items = useSymbols((s) => s.items);

  // нормализуем и дедупим список символов
  const symbols = useMemo(() => {
    const set = new Set(
      items
        .map((i) => i.symbol?.trim().toUpperCase())
        .filter((s): s is string => Boolean(s))
    );
    return Array.from(set);
  }, [items]);

  const symbolsKey = useMemo(() => symbols.slice().sort().join(","), [symbols]);

  // SSE котировки
  useSSEQuotes(symbols);

  const setMetrics = useMetrics((s) => s.setSnapshot);
  const setPositions = useMarket((s) => s.setPositions);
  const setOrdersFromSnapshot = useOrders((s) => s.setFromSnapshot);

  // защита от одновременных обновлений + отмена устаревших
  const inflightRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const refreshAll = async () => {
    if (!symbols.length) return;

    // не позволяем наложиться следующему тіку
    if (inflightRef.current) return;
    inflightRef.current = true;

    // отменяем предыдущий «устаревший» ран
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const [metricsRes, positionsRes, uiSnapRes] = await Promise.allSettled([
        apiGetMetrics(),
        apiGetPositions(symbols),
        apiGetUISnapshot(["orders", "fills"]),
      ]);

      if (metricsRes.status === "fulfilled") setMetrics(metricsRes.value);
      if (positionsRes.status === "fulfilled") setPositions(positionsRes.value);
      if (uiSnapRes.status === "fulfilled") {
        const snap = uiSnapRes.value as UISnapshot;
        setOrdersFromSnapshot({
          orders: snap.orders ?? [],
          fills: snap.fills ?? [],
        });
      }
    } finally {
      inflightRef.current = false;
    }
  };

  // первичный фетч при изменении набора символов
  useEffect(() => {
    if (!symbols.length) return;
    refreshAll();
    // отменяем при размонтировании/смене набора
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey]);

  // периодический поллинг (не запустится, если символов нет)
  useInterval(() => refreshAll(), symbols.length ? 2000 : null);

  return (
    <div className="min-h-screen">
      <TopBar />
      <div className="mx-auto max-w-7xl p-4">
        {items.length === 0 ? (
          <div className="rounded-2xl border border-zinc-700/80 bg-zinc-800/50 p-4 text-zinc-300">
            Пока пусто — добавьте символ вверху.
          </div>
        ) : (
          // ⬇️ Force single-column, centered cards
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 justify-center items-start">
            {items.map((it) => (
              <SymbolCard key={it.symbol} symbol={it.symbol} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
