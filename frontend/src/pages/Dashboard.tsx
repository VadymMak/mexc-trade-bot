import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import TopBar from "@/components/layout/TopBar";
import SymbolCard from "@/components/cards/SymbolCard";
import { useSymbols } from "@/store/symbols";
import { useMarket } from "@/store/market";
import { useMetrics } from "@/store/metrics";
import { usePositionsStore } from "@/store/positions";
import type { MarkGetter } from "@/components/cards/PositionSummary";
import {
  apiGetMetrics,
  apiGetExecPositions,
  apiGetUISnapshot,
  apiOpenSession,
} from "@/api/api";
import { useInterval } from "@/hooks/useInterval";
import { useOrders } from "@/store/orders";
import { useBoot } from "@/store/boot";
import type { UISnapshot } from "@/types/api";

import PositionSummary from "@/components/cards/PositionSummary";
import PositionsTable from "@/components/cards/PositionsTable";

type UIStateMaybe = { watchlist?: unknown; data?: { watchlist?: unknown } };
type UISnapshotWithUI = UISnapshot & { ui_state?: UIStateMaybe };

function toUpperSymbolList(x: unknown): string[] {
  if (!Array.isArray(x)) return [];
  const out: string[] = [];
  for (const v of x) {
    if (typeof v === "string") {
      const s = v.trim().toUpperCase();
      if (s) out.push(s);
    }
  }
  return out;
}

export default function Dashboard() {
  // 1) wait for symbols rehydrate
  const [hydrated, setHydrated] = useState(
    useSymbols.persist?.hasHydrated?.() ?? false
  );
  useEffect(() => {
    const off = useSymbols.persist?.onFinishHydration?.(() => setHydrated(true));
    return () => off?.();
  }, []);

  // 2) stores
  const items         = useSymbols((s) => s.items);
  const ensureSymbols = useSymbols((s) => s.ensureSymbols);

  const setMetrics         = useMetrics((s) => s.setSnapshot);
  const setMarketPositions = useMarket((s) => s.setPositions);
  const clearMarket        = useMarket((s) => s.clear);
  const quoteOf            = useMarket((s) => s.quoteOf);
  const setPositionsStore  = usePositionsStore((s) => s.setPositions);
  const setOrdersSnapshot  = useOrders((s) => s.setFromSnapshot);

  const bootApp    = useBoot((s) => s.bootApp);
  const bootStatus = useBoot((s) => s.status);
  const bootError  = useBoot((s) => s.error);

  // 3) normalized symbols
  const symbols = useMemo(() => {
    if (!hydrated) return [];
    const set = new Set(
      items.map((i) => i.symbol?.trim().toUpperCase()).filter((s): s is string => !!s)
    );
    return Array.from(set);
  }, [hydrated, items]);
  const symbolsKey = useMemo(() => symbols.slice().sort().join(","), [symbols]);
  const hasSymbols = symbols.length > 0;

  // 4) mark getter (mid from L1)
  const getMarkPrice = useCallback<MarkGetter>((symbol: string) => {
    const sym = (symbol || "").trim().toUpperCase();
    const q = quoteOf(sym);
    const bid = typeof q?.bid === "number" ? q.bid : undefined;
    const ask = typeof q?.ask === "number" ? q.ask : undefined;
    if (Number.isFinite(bid ?? NaN) && Number.isFinite(ask ?? NaN)) {
      return ((bid as number) + (ask as number)) / 2;
    }
    return (bid ?? ask) as number | undefined;
  }, [quoteOf]);

  // 5) flags
  const inflightRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const ensuredOnceRef = useRef(false);

  // 6) refresh bundle
  const refreshAll = useCallback(async () => {
    if (!hydrated || !hasSymbols) return;
    if (inflightRef.current) return;
    inflightRef.current = true;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const [metricsRes, positionsRes, uiSnapRes] = await Promise.allSettled([
        apiGetMetrics(),
        apiGetExecPositions(symbols),
        apiGetUISnapshot(["positions", "orders", "fills"]),
      ]);

      if (metricsRes.status === "fulfilled") setMetrics(metricsRes.value);

      if (positionsRes.status === "fulfilled") {
        const ps = positionsRes.value;
        setMarketPositions(ps);
        setPositionsStore(ps);

        if (!ensuredOnceRef.current && ps && ps.length) {
          ensuredOnceRef.current = true;
          const backendSymbols = ps.map((p) => p.symbol).filter(Boolean);
          if (backendSymbols.length) ensureSymbols(backendSymbols);
        }
      }

      if (uiSnapRes.status === "fulfilled") {
        const snap = uiSnapRes.value as UISnapshot;
        setOrdersSnapshot({ orders: snap.orders ?? [], fills: snap.fills ?? [] });
      }
    } finally {
      inflightRef.current = false;
    }
  }, [
    hydrated, hasSymbols, symbols,
    setMetrics, setMarketPositions, setPositionsStore,
    ensureSymbols, setOrdersSnapshot,
  ]);

  // 7) try to restore watchlist once
  useEffect(() => {
    if (!hydrated) return;
    let cancelled = false;
    (async () => {
      try {
        const raw = (await apiGetUISnapshot()) as UISnapshotWithUI;
        if (cancelled || !raw) return;
        const wl1 = toUpperSymbolList(raw.ui_state?.watchlist);
        const wl2 = toUpperSymbolList(raw.ui_state?.data?.watchlist);
        const wl = wl1.length ? wl1 : wl2;
        if (wl.length && !hasSymbols) ensureSymbols(wl);
      } catch { /* non-fatal */ }
    })();
    return () => { cancelled = true; };
  }, [hydrated, ensureSymbols, hasSymbols]);

  // 8) boot on watchlist change
  useEffect(() => {
    if (!hydrated || !hasSymbols) return;
    let cancelled = false;
    (async () => {
      try {
        ensuredOnceRef.current = false;
        clearMarket();
        await apiOpenSession(false);
        if (!cancelled) {
          await bootApp();
          await refreshAll();
        }
      } catch {/* handled in useBoot */}
    })();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [hydrated, hasSymbols, symbolsKey, clearMarket, bootApp, refreshAll]);

  // 9) polling
  useInterval(() => refreshAll(), hydrated && hasSymbols ? 5000 : null);

  // 10) loading/error
  if (!hydrated || bootStatus === "loading") {
    return (
      <div className="min-h-screen">
        <TopBar />
        <div className="mx-auto max-w-7xl p-4 text-zinc-300">
          {hydrated ? "Booting…" : "Loading watchlist…"}
        </div>
      </div>
    );
  }

  if (bootStatus === "error") {
    return (
      <div className="min-h-screen">
        <TopBar />
        <div className="mx-auto max-w-7xl p-4 text-rose-300">
          Failed to boot{bootError ? `: ${String(bootError)}` : ""}.
        </div>
      </div>
    );
  }

  // 11) classic layout: Summary + PositionsTable on top, cards below
  return (
    <div className="min-h-screen">
      <TopBar />
      <div className="mx-auto max-w-7xl p-4">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
          <div className="lg:col-span-1">
            <PositionSummary compact getMarkPrice={getMarkPrice} />
          </div>
          <div className="lg:col-span-2">
            <PositionsTable />
          </div>
        </div>

        {items.length === 0 ? (
          <div className="rounded-2xl border border-zinc-700/80 bg-zinc-800/50 p-4 text-zinc-300">
            Пока пусто — добавьте символ вверху.
          </div>
        ) : (
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
