// src/store/boot.ts
import { create } from "zustand";
import { apiOpenSession, apiGetUISnapshot, apiWatchlistBulk } from "@/api/api";
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

/* ---------- type guards for watchlist responses ---------- */
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

function isWatchlistSymbols(v: unknown): v is { symbols: string[] } {
  return (
    typeof v === "object" &&
    v !== null &&
    Array.isArray((v as { symbols?: unknown }).symbols) &&
    (v as { symbols: unknown[] }).symbols.every((x) => typeof x === "string")
  );
}

export const useBoot = create<BootState>((set, get) => ({
  status: "idle",
  error: null,

  resetBoot: () => set({ status: "idle", error: null }),

  bootApp: async () => {
    if (get().status === "loading") return;
    set({ status: "loading", error: null });

    try {
      // 1) Sync provider/mode
      await useProvider.getState().load();

      // 2) Open/resume UI session (no reset)
      await apiOpenSession(false);

      // 3) Fetch initial snapshot (positions + orders/fills)
      const snap = await apiGetUISnapshot(["positions", "orders", "fills"]);
      useMarket.getState().setPositions(snap?.positions ?? []);

      useOrders.getState().setFromSnapshot({
        orders: Array.isArray(snap?.orders) ? snap!.orders : [],
        fills: Array.isArray(snap?.fills) ? snap!.fills : [],
      });

      // Debug
      console.debug("[boot] seeded orders/fills", {
        ETH_orders: useOrders.getState().ordersOf("ETHUSDT").length,
        ETH_fills: useOrders.getState().fillsOf("ETHUSDT").length,
        SOL_orders: useOrders.getState().ordersOf("SOLUSDT").length,
        SOL_fills: useOrders.getState().fillsOf("SOLUSDT").length,
      });

      // 4) Fetch and seed watchlist
      const wl: unknown = await apiWatchlistBulk([]);

      if (isWatchlistItems(wl)) {
        // Map optional running -> required boolean
        const items = wl.items.map((it) => ({
          symbol: it.symbol,
          running: Boolean(it.running),
        }));
        useSymbols.getState().replace(items);
      } else if (isWatchlistSymbols(wl)) {
        useSymbols.getState().ensureSymbols(wl.symbols);
      }

      set({ status: "ready", error: null });
    } catch (err) {
      set({ status: "error", error: err });
      throw err;
    }
  },
}));
