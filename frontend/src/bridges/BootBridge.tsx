// src/bridges/BootBridge.tsx
import { useEffect, useRef } from "react";
import { useOrders } from "@/store/orders";
import type { OrderItem, FillItem, Position } from "@/types";

type BootPayload = {
  ui_state?: { watchlist?: { symbols?: string[] } };
  positions?: Position[];
  orders?: OrderItem[];
  fills?: FillItem[];
};

// Primary and fallback endpoints (keep if your backend differs)
const BOOT_URL = "/api/ui/state";
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
        // 1) Try the canonical boot payload
        let res = await fetch(BOOT_URL, { signal: ac.signal });
        if (!res.ok) {
          // 2) Graceful fallback to snapshot (orders + fills only)
          if (res.status === 404 || res.status === 405) {
            res = await fetch(FALLBACK_URL, { signal: ac.signal });
          }
          if (!res.ok) throw new Error(`Boot fetch failed: ${res.status}`);
        }

        const raw: unknown = await res.json();
        const data: BootPayload = (raw && typeof raw === "object") ? (raw as BootPayload) : {};

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
