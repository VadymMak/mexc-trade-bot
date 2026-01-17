// src/bridges/BootBridge.tsx
import { useEffect, useRef } from "react";
import { useOrders } from "@/store/orders";
import http from "@/lib/http";
import type { OrderItem, FillItem, Position } from "@/types";

type BootPayload = {
  ui_state?: { watchlist?: { symbols?: string[] } };
  positions?: Position[];
  orders?: OrderItem[];
  fills?: FillItem[];
};

// Primary and fallback endpoints
const BOOT_URL = "/api/ui/snapshot?include=positions,orders,fills";
const FALLBACK_URL = "/api/ui/snapshot?include=orders,fills";

function isOrderArray(a: unknown): a is OrderItem[] {
  return Array.isArray(a) && a.every((x) => x && typeof x === "object" && "symbol" in (x as object));
}
function isFillArray(a: unknown): a is FillItem[] {
  return Array.isArray(a) && a.every((x) => x && typeof x === "object" && "symbol" in (x as object));
}

export default function BootBridge() {
  const loaded = useRef(false);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;

    const ac = new AbortController();

    (async () => {
      try {
        let data: BootPayload = {};
        
        try {
          // 1) Try the canonical boot payload
          const res = await http.get<BootPayload>(BOOT_URL, { signal: ac.signal });
          data = res.data && typeof res.data === "object" ? res.data : {};
        } catch (err: unknown) {
          // 2) Graceful fallback to snapshot (orders + fills only)
          if ((err as { response?: { status: number } }).response?.status === 404 || (err as { response?: { status: number } }).response?.status === 405) {
            const fallbackRes = await http.get<BootPayload>(FALLBACK_URL, { signal: ac.signal });
            data = fallbackRes.data && typeof fallbackRes.data === "object" ? fallbackRes.data : {};
          } else {
            throw err;
          }
        }

        const ordersArr = isOrderArray(data.orders) ? data.orders : [];
        const fillsArr  = isFillArray(data.fills)   ? data.fills  : [];

        // Hydrate orders store (dedup & sort happens inside)
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