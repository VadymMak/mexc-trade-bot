// src/hooks/usePositions.ts
import { useCallback, useEffect, useMemo, useRef } from "react";
import { usePositionsStore, type Position } from "@/store/positions";

const norm = (s: string) => (s || "").trim().toUpperCase();

export type UsePositionsOptions = {
  symbols?: string[];
  intervalMs?: number;
  immediate?: boolean;
  pauseWhenHidden?: boolean;
};

export type UsePositionsResult = {
  positions: Position[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

export function usePositions(options?: UsePositionsOptions): UsePositionsResult {
  const intervalMs = options?.intervalMs ?? 3000;
  const immediate = options?.immediate ?? true;
  const pauseWhenHidden = options?.pauseWhenHidden ?? true;

  // Normalize requested symbols
  const normalizedSymbols = useMemo<string[] | undefined>(() => {
    const list = options?.symbols;
    if (!list || list.length === 0) return undefined;
    return list.map(norm);
  }, [options?.symbols]);

  // ⚠️ Select raw store slices ONLY; derive outside the selector.
  const loadAll           = usePositionsStore((s) => s.loadAll);
  const loading           = usePositionsStore((s) => s.loading);
  const error             = usePositionsStore((s) => s.error);
  const positionsBySymbol = usePositionsStore((s) => s.positionsBySymbol);

  // Derive positions array from the raw object (keeps getSnapshot stable)
  const positions = useMemo<Position[]>(
    () => Object.values(positionsBySymbol),
    [positionsBySymbol]
  );

  // Keep latest symbols for the polling callback
  const symbolsRef = useRef<string[] | undefined>(normalizedSymbols);
  useEffect(() => {
    symbolsRef.current = normalizedSymbols;
  }, [normalizedSymbols]);

  const refresh = useCallback(async () => {
    await loadAll(symbolsRef.current);
  }, [loadAll]);

  // Initial load (optional)
  useEffect(() => {
    if (!immediate) return;
    void refresh();
  }, [immediate, refresh]);

  // Visibility-aware polling
  useEffect(() => {
    const shouldRun = (): boolean => {
      if (!pauseWhenHidden) return true;
      if (typeof document === "undefined") return true;
      return document.visibilityState === "visible";
    };

    const tick = () => {
      if (shouldRun()) void refresh();
    };

    const id = window.setInterval(tick, Math.max(250, intervalMs));

    const onVisibility = () => {
      if (shouldRun()) void refresh();
    };
    if (pauseWhenHidden && typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      clearInterval(id);
      if (pauseWhenHidden && typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, [intervalMs, pauseWhenHidden, refresh]);

  return { positions, loading, error, refresh };
}
