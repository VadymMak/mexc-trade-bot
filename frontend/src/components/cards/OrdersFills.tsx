// src/components/cards/OrdersFills.tsx
import { useMemo, memo } from "react";
import { useOrders } from "@/store/orders";
import type { OrderItem, FillItem } from "@/types/api";

type Props = { symbol: string; limit?: number };

/* -------------------------------- utils -------------------------------- */
function formatTs(ts?: number | string | null): string {
  if (!ts) return "—";
  const d = typeof ts === "number" ? new Date(ts) : new Date(ts);
  const t = d.getTime();
  if (!Number.isFinite(t)) return "—";
  return d.toLocaleTimeString(undefined, { hour12: false });
}

function toNum(v: unknown, digits = 6): number | string {
  if (typeof v === "number" && Number.isFinite(v)) return +v.toFixed(digits);
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return String(v);
}

/* --------- narrow structural types for backend extras (no any) --------- */
type OrderLike = OrderItem & {
  submitted_at?: string;
  ts_ms?: number;
  avg_fill_price?: number | null;
  status?: string | null;
};

type FillLike = FillItem & {
  executed_at?: string;
  ts_ms?: number;
  fee?: number | null;
};

const getOrderTime = (o: OrderLike): number => {
  const t: string | number | undefined = o.submitted_at ?? o.ts_ms;
  if (typeof t === "number") return Number.isFinite(t) ? t : 0;
  if (typeof t === "string") {
    const ms = Date.parse(t);
    return Number.isFinite(ms) ? ms : 0;
  }
  return 0;
};

const getFillTime = (f: FillLike): number => {
  const t: string | number | undefined = f.executed_at ?? f.ts_ms;
  if (typeof t === "number") return Number.isFinite(t) ? t : 0;
  if (typeof t === "string") {
    const ms = Date.parse(t);
    return Number.isFinite(ms) ? ms : 0;
  }
  return 0;
};

const getOrderPrice = (o: OrderLike): number | null | undefined =>
  o.price ?? o.avg_fill_price;

const getOrderStatus = (o: OrderLike): string | null =>
  typeof o.status === "string" ? o.status : null;

const getFillFee = (f: FillLike): number | null =>
  typeof f.fee === "number" ? f.fee : null;

const statusClass = (s?: string | null): string => {
  const v = (s ?? "").toUpperCase();
  if (v === "FILLED") return "text-emerald-300";
  if (v === "REJECTED") return "text-rose-300";
  if (v === "CANCELED" || v === "CANCELLED") return "text-zinc-400";
  return "text-zinc-300";
};

/** Keep only the latest item per id */
function dedupeLatestById<T extends { id: string | number }>(
  items: ReadonlyArray<T>,
  getTime: (x: T) => number
): T[] {
  const map = new Map<string | number, T>();
  for (const it of items) {
    const prev = map.get(it.id);
    if (!prev || getTime(it) > getTime(prev)) map.set(it.id, it);
  }
  return Array.from(map.values());
}

/* ----------------------------- rows ----------------------------- */

const OrderRow = memo(function OrderRow({ o }: { o: OrderLike }) {
  return (
    <tr className="text-xs odd:bg-zinc-900/30 hover:bg-zinc-900/50 border-b border-zinc-800/60">
      <td className="px-2 py-1 w-[84px]">{formatTs(o.submitted_at ?? o.ts_ms)}</td>
      <td className={`px-2 py-1 w-[52px] ${o.side === "BUY" ? "text-emerald-400" : "text-rose-400"}`}>
        {o.side}
      </td>
      <td className="px-2 py-1 w-[56px] font-mono tabular-nums text-right">{toNum(o.qty)}</td>
      <td className="px-2 py-1 w-[88px] font-mono tabular-nums text-right">
        {toNum(getOrderPrice(o))}
      </td>
      <td className={`px-2 py-1 w-[84px] ${statusClass(getOrderStatus(o))}`}>
        {(getOrderStatus(o) || "—").toString()}
      </td>
    </tr>
  );
});

const FillRow = memo(function FillRow({ f }: { f: FillLike }) {
  return (
    <tr className="text-xs odd:bg-zinc-900/30 hover:bg-zinc-900/50 border-b border-zinc-800/60">
      <td className="px-2 py-1 w-[84px]">{formatTs(f.executed_at ?? f.ts_ms)}</td>
      <td className={`px-2 py-1 w-[52px] ${f.side === "BUY" ? "text-emerald-400" : "text-rose-400"}`}>
        {f.side}
      </td>
      <td className="px-2 py-1 w-[56px] font-mono tabular-nums text-right">{toNum(f.qty)}</td>
      <td className="px-2 py-1 w-[88px] font-mono tabular-nums text-right">{toNum(f.price)}</td>
      <td className="px-2 py-1 w-[60px] font-mono tabular-nums text-right">
        {toNum(getFillFee(f) ?? 0, 8)}
      </td>
    </tr>
  );
});

/* ----------------------------- component ----------------------------- */

const OrdersFills = memo(function OrdersFills({ symbol, limit = 50 }: Props) {
  // Use per-symbol selectors to avoid rerenders on other symbols’ updates
  const allOrders = useOrders((s) => s.ordersOf(symbol));
  const allFills  = useOrders((s) => s.fillsOf(symbol));

  const orders = useMemo(() => {
    const deduped = dedupeLatestById(allOrders as OrderLike[], getOrderTime);
    return deduped.sort((a, b) => getOrderTime(b) - getOrderTime(a)).slice(0, limit);
  }, [allOrders, limit]);

  const fills = useMemo(() => {
    const deduped = dedupeLatestById(allFills as FillLike[], getFillTime);
    return deduped.sort((a, b) => getFillTime(b) - getFillTime(a)).slice(0, limit);
  }, [allFills, limit]);

  return (
    <section className="rounded-xl border border-zinc-800/50 bg-zinc-900/30 p-2 flex flex-col min-h-0">
      <div className="text-zinc-300 text-sm font-medium mb-2">Orders / Fills</div>

      <div className="grid grid-cols-2 gap-2 flex-1 min-h-0">
        {/* Orders */}
        <div className="flex flex-col min-h-0">
          <div className="text-xs text-zinc-400 mb-1">Orders</div>
          <div
            className="rounded-lg border border-zinc-800/60 bg-black/20 overflow-y-auto scrollbar-thin
                       h-[112px] min-h-[112px]"
            aria-label="Orders table viewport"
          >
            <table className="w-full table-fixed text-xs text-zinc-200">
              <thead className="sticky top-0 bg-zinc-900/70 backdrop-blur">
                <tr className="[&>th]:px-2 [&>th]:py-1 [&>th]:text-left">
                  <th className="w-[84px]">Time</th>
                  <th className="w-[52px]">Side</th>
                  <th className="w-[56px] text-right">Qty</th>
                  <th className="w-[88px] text-right">Price</th>
                  <th className="w-[84px]">Status</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {orders.length === 0 ? (
                  <tr>
                    <td className="px-2 py-2 text-xs text-zinc-400" colSpan={5}>
                      No orders yet.
                    </td>
                  </tr>
                ) : (
                  orders.map((o) => (
                    <OrderRow key={`order:${symbol}:${String(o.id)}`} o={o} />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Fills */}
        <div className="flex flex-col min-h-0">
          <div className="text-xs text-zinc-400 mb-1">Fills</div>
          <div
            className="rounded-lg border border-zinc-800/60 bg-black/20 overflow-y-auto scrollbar-thin
                       h-[112px] min-h-[112px]"
            aria-label="Fills table viewport"
          >
            <table className="w-full table-fixed text-xs text-zinc-200">
              <thead className="sticky top-0 bg-zinc-900/70 backdrop-blur">
                <tr className="[&>th]:px-2 [&>th]:py-1 [&>th]:text-left">
                  <th className="w-[84px]">Time</th>
                  <th className="w-[52px]">Side</th>
                  <th className="w-[56px] text-right">Qty</th>
                  <th className="w-[88px] text-right">Price</th>
                  <th className="w-[60px] text-right">Fee</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {fills.length === 0 ? (
                  <tr>
                    <td className="px-2 py-2 text-xs text-zinc-400" colSpan={5}>
                      No fills yet.
                    </td>
                  </tr>
                ) : (
                  fills.map((f) => (
                    <FillRow key={`fill:${symbol}:${String(f.id)}`} f={f} />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
});

export default OrdersFills;
