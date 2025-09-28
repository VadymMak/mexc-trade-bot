import { useMemo, useState } from "react";
import PositionSummary from "@/components/cards/PositionSummary";
import PositionsTable from "@/components/cards/PositionsTable";
import { useOrders } from "@/store/orders";

type TradingSidebarProps = {
  /** Getter used by PositionSummary for mark price */
  getMarkPrice: (symbol: string) => number | undefined;
  className?: string;
};

type OrderRow = {
  id?: string | number;
  ts_ms?: number;
  symbol?: string;
  side?: string;
  qty?: number;
  price?: number;
  status?: string;
};
type FillRow = {
  id?: string | number;
  ts_ms?: number;
  symbol?: string;
  side?: string;
  qty?: number;
  price?: number;
  upnl?: number;
};

function fmtNum(n: number, fractionDigits = 6): string {
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: fractionDigits,
  });
}
function fmtUsd(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Safely flattens Record<string, T[]> to T[] with runtime guards */
function flattenRecordArrays<T>(rec: unknown): T[] {
  if (!rec || typeof rec !== "object") return [];
  const out: T[] = [];
  for (const v of Object.values(rec as Record<string, unknown>)) {
    if (Array.isArray(v)) {
      for (const item of v) out.push(item as T);
    }
  }
  return out;
}

export default function TradingSidebar({
  getMarkPrice,
  className,
}: TradingSidebarProps) {
  const [tab, setTab] = useState<"positions" | "orders" | "fills">("positions");

  // Store selectors return maps; flatten them to arrays for rendering
  const ordersMap = useOrders((s) => s.orders) as unknown;
  const fillsMap  = useOrders((s) => s.fills)  as unknown;

  const orders: OrderRow[] = useMemo(
    () => flattenRecordArrays<OrderRow>(ordersMap),
    [ordersMap]
  );
  const fills: FillRow[] = useMemo(
    () => flattenRecordArrays<FillRow>(fillsMap),
    [fillsMap]
  );

  const TabBtn = ({
    id,
    label,
  }: {
    id: "positions" | "orders" | "fills";
    label: string;
  }) => (
    <button
      className={[
        "px-3 py-1.5 rounded-lg border text-sm transition",
        tab === id
          ? "bg-neutral-800/70 border-neutral-700 text-neutral-100"
          : "bg-transparent border-transparent text-neutral-400 hover:bg-neutral-900/60 hover:text-neutral-200",
      ].join(" ")}
      onClick={() => setTab(id)}
      type="button"
    >
      {label}
    </button>
  );

  return (
    <aside
      className={[
        // sticky sidebar under the TopBar; adjust 84px if your header height differs
        "sticky top-[84px]",
        "flex flex-col shrink-0 w-[340px] min-w-[320px] max-w-[420px]",
        "max-h-[calc(100vh-100px)]", // leave a little extra room below the header
        "rounded-2xl border border-neutral-800 bg-neutral-950/70 shadow-lg",
        "overflow-hidden", // keep rounded corners on header while body scrolls
        className || "",
      ].join(" ")}
    >
      {/* Summary */}
      <div className="p-3 border-b border-neutral-800">
        <PositionSummary compact getMarkPrice={getMarkPrice} />
      </div>

      {/* Tabs */}
      <div className="px-3 py-2 border-b border-neutral-800 flex items-center gap-2">
        <TabBtn id="positions" label="Positions" />
        <TabBtn id="orders" label="Orders" />
        <TabBtn id="fills" label="Fills" />
      </div>

      {/* Body (independent scroll) */}
      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain">
        {tab === "positions" && (
          <div className="p-0">
            <PositionsTable variant="sidebar" />
          </div>
        )}

        {tab === "orders" && (
          <div className="p-3">
            <table className="min-w-full text-xs md:text-sm text-neutral-200">
              <thead className="sticky top-0 z-10 bg-neutral-900/80 backdrop-blur text-neutral-400 border-b border-neutral-800">
                <tr>
                  <th className="px-3 py-2 text-left">Time</th>
                  <th className="px-3 py-2 text-left">Symbol</th>
                  <th className="px-3 py-2 text-right">Side</th>
                  <th className="px-3 py-2 text-right">Qty</th>
                  <th className="px-3 py-2 text-right">Price</th>
                  <th className="px-3 py-2 text-right">Status</th>
                </tr>
              </thead>
              <tbody>
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-neutral-500">
                      No orders yet
                    </td>
                  </tr>
                ) : (
                  orders.slice().reverse().map((o, idx) => (
                    <tr
                      key={String(o.id ?? `${o.symbol}-${o.ts_ms}-${idx}`)}
                      className="border-t border-neutral-800/70"
                    >
                      <td className="px-3 py-2">
                        {new Date(o.ts_ms ?? Date.now()).toLocaleTimeString()}
                      </td>
                      <td className="px-3 py-2">{o.symbol ?? "—"}</td>
                      <td className="px-3 py-2 text-right">{o.side ?? "—"}</td>
                      <td className="px-3 py-2 text-right">{fmtNum(o.qty ?? NaN, 6)}</td>
                      <td className="px-3 py-2 text-right">{fmtNum(o.price ?? NaN, 6)}</td>
                      <td className="px-3 py-2 text-right">{o.status ?? "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {tab === "fills" && (
          <div className="p-3">
            <table className="min-w-full text-xs md:text-sm text-neutral-200">
              <thead className="sticky top-0 z-10 bg-neutral-900/80 backdrop-blur text-neutral-400 border-b border-neutral-800">
                <tr>
                  <th className="px-3 py-2 text-left">Time</th>
                  <th className="px-3 py-2 text-left">Symbol</th>
                  <th className="px-3 py-2 text-right">Side</th>
                  <th className="px-3 py-2 text-right">Qty</th>
                  <th className="px-3 py-2 text-right">Price</th>
                  <th className="px-3 py-2 text-right">uPnL</th>
                </tr>
              </thead>
              <tbody>
                {fills.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-neutral-500">
                      No fills yet
                    </td>
                  </tr>
                ) : (
                  fills.slice().reverse().map((f, idx) => (
                    <tr
                      key={String(f.id ?? `${f.symbol}-${f.ts_ms}-${idx}`)}
                      className="border-t border-neutral-800/70"
                    >
                      <td className="px-3 py-2">
                        {new Date(f.ts_ms ?? Date.now()).toLocaleTimeString()}
                      </td>
                      <td className="px-3 py-2">{f.symbol ?? "—"}</td>
                      <td className="px-3 py-2 text-right">{f.side ?? "—"}</td>
                      <td className="px-3 py-2 text-right">{fmtNum(f.qty ?? NaN, 6)}</td>
                      <td className="px-3 py-2 text-right">{fmtNum(f.price ?? NaN, 6)}</td>
                      <td className="px-3 py-2 text-right">{fmtUsd(f.upnl ?? NaN)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </aside>
  );
}
