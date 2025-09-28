// src/bridges/BootBridge.tsx
import { useEffect, useRef } from "react";
import { useOrders } from "@/store/orders";
import type { OrderItem, FillItem, Position } from "@/types/api";

type BootPayload = {
  ui_state?: { watchlist?: { symbols?: string[] } };
  positions?: Position[];
  orders?: OrderItem[];
  fills?: FillItem[];
};

// Keep as-is unless your backend path differs
const BOOT_URL = "/api/ui/state";

function isOrderArray(a: unknown): a is OrderItem[] {
  return Array.isArray(a) && a.every((x) => x && typeof x === "object" && "symbol" in x);
}
function isFillArray(a: unknown): a is FillItem[] {
  return Array.isArray(a) && a.every((x) => x && typeof x === "object" && "symbol" in x);
}

export default function BootBridge() {
  const loaded = useRef(false);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;

    const ac = new AbortController();

    (async () => {
      try {
        const res = await fetch(BOOT_URL, { signal: ac.signal });
        if (!res.ok) throw new Error(`Boot fetch failed: ${res.status}`);

        const raw = (await res.json()) as unknown;
        const data: BootPayload = (raw && typeof raw === "object") ? (raw as BootPayload) : {};

        const ordersArr = isOrderArray(data.orders) ? data.orders : [];
        const fillsArr  = isFillArray(data.fills)   ? data.fills  : [];

        useOrders.getState().setFromSnapshot({ orders: ordersArr, fills: fillsArr });

        // Debug confirmation
        const st = useOrders.getState();
        console.debug("[BootBridge] hydrated", {
          ETH_orders: st.ordersOf("ETHUSDT").length,
          ETH_fills:  st.fillsOf("ETHUSDT").length,
          SOL_orders: st.ordersOf("SOLUSDT").length,
          SOL_fills:  st.fillsOf("SOLUSDT").length,
        });
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          console.debug("[BootBridge] fetch aborted (component unmounted)");
          return;
        }
        console.warn("[BootBridge] failed to load boot state:", err);
      }
    })();

    return () => ac.abort();
  }, []);

  return null;
}
