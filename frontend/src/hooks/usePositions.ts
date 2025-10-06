// src/hooks/usePositions.ts
import { useCallback, useEffect, useMemo, useRef } from "react";
import { usePositionsStore } from "@/store/positions";
import {type Position} from '@/types/index'
import { useProvider } from "@/store/provider";

const normalizeSymbol = (s: string): string => (s || "").trim().toUpperCase();

/* ───────────────────────── Types ───────────────────────── */

export interface UsePositionsOptions {
  /** Ограничение по символам (опционально) */
  symbols?: string[];
  /** Интервал опроса в миллисекундах */
  intervalMs?: number;
  /** Выполнять ли первый запрос сразу при маунте */
  immediate?: boolean;
  /** Приостанавливать ли опрос, когда вкладка скрыта */
  pauseWhenHidden?: boolean;
}

export interface UsePositionsResult {
  positions: Position[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/* ───────────────────────── Hook ───────────────────────── */

export function usePositions(options?: UsePositionsOptions): UsePositionsResult {
  const intervalMs = options?.intervalMs ?? 3000;
  const immediate = options?.immediate ?? true;
  const pauseWhenHidden = options?.pauseWhenHidden ?? true;

  // Нормализуем список символов
  const normalizedSymbols = useMemo<string[] | undefined>(() => {
    const list = options?.symbols;
    if (!list || list.length === 0) return undefined;
    return list.map(normalizeSymbol);
  }, [options?.symbols]);

  // Достаём функции и состояние из стора
  const loadAll = usePositionsStore((s) => s.loadAll);
  const loading = usePositionsStore((s) => s.loading);
  const error = usePositionsStore((s) => s.error);
  const positionsBySymbol = usePositionsStore((s) => s.positionsBySymbol);

  // Проверка готовности провайдера
  const providerReady = useProvider((s) => !!s.active && !!s.mode);

  // Вычисляем массив позиций
  const positions = useMemo<Position[]>(() => Object.values(positionsBySymbol), [positionsBySymbol]);

  // Храним актуальный список символов для refresh()
  const symbolsRef = useRef<string[] | undefined>(normalizedSymbols);
  useEffect(() => {
    symbolsRef.current = normalizedSymbols;
  }, [normalizedSymbols]);

  // Предотвращаем пересечение запросов
  const inflightRef = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    if (!providerReady) return; // не дёргаем API до готовности провайдера
    if (inflightRef.current) return inflightRef.current;

    const task = loadAll(symbolsRef.current)
      .catch(() => {
        /* ошибки уже обрабатываются в сторе */
      })
      .finally(() => {
        inflightRef.current = null;
      });

    inflightRef.current = task;
    await task;
  }, [loadAll, providerReady]);

  // Первичная загрузка
  useEffect(() => {
    if (immediate) void refresh();
  }, [immediate, refresh]);

  // Polling с паузой при скрытии вкладки
  useEffect(() => {
    if (!providerReady) return;

    let timerId: number | null = null;

    const shouldRun = (): boolean => {
      if (!pauseWhenHidden) return true;
      if (typeof document === "undefined") return true;
      return document.visibilityState === "visible";
    };

    const tick = (): void => {
      if (shouldRun()) void refresh();
    };

    const startPolling = (): void => {
      stopPolling(); // очистить на случай двойного запуска
      timerId = window.setInterval(tick, Math.max(250, intervalMs));
    };

    const stopPolling = (): void => {
      if (timerId !== null) {
        clearInterval(timerId);
        timerId = null;
      }
    };

    // Автообновление при смене видимости вкладки
    const handleVisibilityChange = (): void => {
      if (shouldRun()) void refresh();
    };

    startPolling();

    if (pauseWhenHidden && typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }

    return () => {
      stopPolling();
      if (pauseWhenHidden && typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
      }
    };
  }, [intervalMs, pauseWhenHidden, refresh, providerReady]);

  return { positions, loading, error, refresh };
}
