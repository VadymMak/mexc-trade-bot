// src/store/boot.ts
import { create } from "zustand";
import { apiOpenSession, apiGetUISnapshot, getWatchlist } from "@/api/api";
import { useProvider } from "./provider";
import { useMarket } from "./market";
import { useSymbols } from "./symbols";
import { useOrders } from "./orders";

export type BootStatus = "idle" | "loading" | "ready" | "error";

interface BootState {
  status: BootStatus;
  error: unknown | null;
  bootApp: () => Promise<void>;
  resetBoot: () => void;
}

/* ---------- safety type guards (на будущее, если бэк вернёт иной формат) ---------- */
function isWatchlistItems(
  v: unknown
): v is { items: { symbol: string; running?: boolean }[] } {
  return (
    typeof v === "object" &&
    v !== null &&
    Array.isArray((v as { items?: unknown }).items) &&
    (v as { items: unknown[] }).items.every(
      (x) => typeof x === "object" && x !== null && "symbol" in (x as object)
    )
  );
}

export const useBoot = create<BootState>((set, get) => ({
  status: "idle",
  error: null,

  resetBoot: () => set({ status: "idle", error: null }),

  bootApp: async () => {
    // не перезапускаем, если уже идёт/завершён бут
    const st = get().status;
    if (st === "loading" || st === "ready") return;

    set({ status: "loading", error: null });

    try {
      // 1) провайдер/режим
      await useProvider.getState().load();

      // 2) открыть/продолжить UI-сессию (без reset)
      await apiOpenSession(false);

      // 3) начальный снапшот (позиции + ордера/сделки)
      const snap = await apiGetUISnapshot(["positions", "orders", "fills"]);
      useMarket.getState().setPositions(snap?.positions ?? []);
      useOrders.getState().setFromSnapshot({
        orders: Array.isArray(snap?.orders) ? snap.orders : [],
        fills: Array.isArray(snap?.fills) ? snap.fills : [],
      });

      // 4) получить и засеять watchlist (ВАЖНО: GET, не POST!)
      const wl = await getWatchlist();
      const items = isWatchlistItems(wl)
        ? wl.items
        : Array.isArray((wl as unknown as { items?: unknown })?.items)
        ? ((wl as unknown as { items: { symbol: string; running?: boolean }[] }).items)
        : [];

      useSymbols
        .getState()
        .replace(items.map((it) => ({ symbol: it.symbol, running: Boolean(it.running) })));

      set({ status: "ready", error: null });

      // debug
      const os = useOrders.getState();
      console.debug("[boot] seeded orders/fills", {
        ETH_orders: os.ordersOf("ETHUSDT").length,
        ETH_fills: os.fillsOf("ETHUSDT").length,
        SOL_orders: os.ordersOf("SOLUSDT").length,
        SOL_fills: os.fillsOf("SOLUSDT").length,
      });
    } catch (err) {
      set({ status: "error", error: err });
      // не rethrow — чтобы не зациклить внешние эффект-хэндлеры
    }
  },
}));
