// src/components/charts/DepthGlass.tsx
import { memo, useEffect, useMemo, useRef, useState } from "react";
import cx from "classnames";

const DEBUG = import.meta.env?.VITE_DEBUG_GLASS === "1";

export type TapeItem = { ts: number; mid: number; spread_bps?: number };

type Props = {
  bid?: number;
  ask?: number;
  bids?: ReadonlyArray<readonly [number, number]>;
  asks?: ReadonlyArray<readonly [number, number]>;
  positionPrice?: number | null;
  tape?: ReadonlyArray<TapeItem>;
  className?: string;

  totalRows?: number;
  rowPx?: number;

  centerSmoothing?: number;
  rebaseThresholdRows?: number;

  minBarPct?: number;

  onPickPrice?: (price: number) => void;
};

/* Defaults */
const DEFAULT_TOTAL_ROWS = 12;
const DEFAULT_ROW_PX = 26;
const DEFAULT_SMOOTH = 0.25;
const DEFAULT_REBASE_ROWS = 4;
const DEFAULT_MIN_BAR = 6;
const QMAX_EMA = 0.25;
const EPS = 1e-9;

/** narrow price label column; bars start right after it */
const LABEL_W_PX = 68;
/** bars may use up to 80% of the whole glass width */
const MAX_BAR_WIDTH_VS_GLASS = 80;

const isFiniteNum = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

type LevelT = Readonly<[number, number]>;
const toTuple = (lv: unknown): LevelT | null => {
  if (Array.isArray(lv) && lv.length >= 2) {
    const p = Number(lv[0]);
    const q = Number(lv[1]);
    return Number.isFinite(p) && p > 0 && Number.isFinite(q) ? ([p, q] as const) : null;
  }
  return null;
};

const fmt = (n?: number, dp = 6) => (isFiniteNum(n) ? n.toFixed(dp) : "—");

function splitRows(asksLen: number, bidsLen: number, total: number): { top: number; bottom: number } {
  const half = Math.floor(total / 2);
  let top = Math.min(asksLen, half);
  let bottom = Math.min(bidsLen, half);
  while (top + bottom < total) {
    if (top < asksLen) { top++; continue; }
    if (bottom < bidsLen) { bottom++; continue; }
    break;
  }
  return { top, bottom };
}

export default memo(function DepthGlass({
  bid,
  ask,
  bids = [],
  asks = [],
  positionPrice,
  tape = [],
  className,
  totalRows = DEFAULT_TOTAL_ROWS,
  rowPx = DEFAULT_ROW_PX,
  centerSmoothing = DEFAULT_SMOOTH,
  rebaseThresholdRows = DEFAULT_REBASE_ROWS,
  minBarPct = DEFAULT_MIN_BAR,
  onPickPrice,
}: Props) {
  // 1) normalize L2
  const rawBids = useMemo<LevelT[]>(() => (bids ?? []).map(toTuple).filter((x): x is LevelT => x !== null), [bids]);
  const rawAsks = useMemo<LevelT[]>(() => (asks ?? []).map(toTuple).filter((x): x is LevelT => x !== null), [asks]);

  // 2) mid from L1 or last tape
  const l1Bid = isFiniteNum(bid) && bid > 0 ? bid : undefined;
  const l1Ask = isFiniteNum(ask) && ask > 0 ? ask : undefined;
  const lastMid = tape && tape.length ? tape[tape.length - 1]?.mid : undefined;
  const realMid = l1Bid != null && l1Ask != null ? (l1Bid + l1Ask) / 2 : (isFiniteNum(lastMid) ? lastMid : undefined);

  const hasData =
    isFiniteNum(realMid) || rawBids.length > 0 || rawAsks.length > 0 || l1Bid != null || l1Ask != null;

  // 3) nearest levels by side
  const sortedBids = useMemo<LevelT[]>(() => {
    if (!isFiniteNum(realMid)) return [];
    return rawBids.filter(([p]) => p <= realMid).sort((a, b) => b[0] - a[0]);
  }, [rawBids, realMid]);

  const sortedAsks = useMemo<LevelT[]>(() => {
    if (!isFiniteNum(realMid)) return [];
    return rawAsks.filter(([p]) => p >= realMid).sort((a, b) => a[0] - b[0]);
  }, [rawAsks, realMid]);

  // 4) rows above/below
  const { top: topCount, bottom: bottomCount } = useMemo(
    () => splitRows(sortedAsks.length, sortedBids.length, Math.max(1, totalRows)),
    [sortedAsks.length, sortedBids.length, totalRows]
  );

  const showAsks = useMemo<LevelT[]>(() => sortedAsks.slice(0, topCount), [sortedAsks, topCount]);
  const showBids = useMemo<LevelT[]>(() => sortedBids.slice(0, bottomCount), [sortedBids, bottomCount]);

  // 5) sliding mid (EMA + rebase)
  const [visualMid, setVisualMid] = useState<number | undefined>(realMid);
  const vmRef = useRef<number | undefined>(realMid);

  useEffect(() => {
    if (!isFiniteNum(realMid)) return;

    if (!isFiniteNum(vmRef.current)) {
      vmRef.current = realMid;
      setVisualMid(realMid);
      return;
    }

    const vm = vmRef.current as number;
    const tick = Math.max(1e-12, l1Ask != null && l1Bid != null ? Math.abs(l1Ask - l1Bid) : Math.abs(realMid * 1e-5));
    const rowsDrift = Math.abs(realMid - vm) / tick;

    let next: number;
    if (rowsDrift > rebaseThresholdRows) {
      next = realMid;
    } else {
      const alpha = Math.max(0, Math.min(1, centerSmoothing));
      next = vm + alpha * (realMid - vm);
    }

    if (Math.abs(next - vm) > EPS) {
      vmRef.current = next;
      setVisualMid(next);
    }
  }, [realMid, centerSmoothing, rebaseThresholdRows, l1Ask, l1Bid]);

  // 6) bar widths (smoothed qMax), capped vs glass
  const qMaxReal = useMemo<number>(() => {
    let qmax = 1;
    for (const [, q] of showBids) qmax = Math.max(qmax, q);
    for (const [, q] of showAsks) qmax = Math.max(qmax, q);
    return qmax;
  }, [showBids, showAsks]);

  const qMaxDispRef = useRef<number>(1);
  useEffect(() => {
    if (qMaxReal > 0) {
      qMaxDispRef.current = Math.max(1, qMaxDispRef.current + QMAX_EMA * (qMaxReal - qMaxDispRef.current));
    }
  }, [qMaxReal]);

  const qMaxDisp = qMaxDispRef.current;

  const barWidthVsGlass = (q: number) => {
    const t = Math.sqrt(Math.max(0, q) / Math.max(1, qMaxDisp));
    const minBar = Math.max(0, Math.min(30, minBarPct));
    const raw = Math.round(t * MAX_BAR_WIDTH_VS_GLASS);
    return Math.max(minBar, Math.min(MAX_BAR_WIDTH_VS_GLASS, raw));
  };

  const handlePick = (p?: number) => {
    if (isFiniteNum(p) && p > 0) onPickPrice?.(p);
  };

  // 7) vertical placement
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [containerH, setContainerH] = useState<number>(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let raf = 0;
    const ro = new ResizeObserver((entries) => {
      const h = entries[0]?.contentRect?.height ?? 0;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setContainerH(h));
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  const rowPct = useMemo(() => {
    const h = containerH || 1;
    return (rowPx / h) * 100;
  }, [containerH, rowPx]);

  const priceToYPct = useMemo(() => {
    const tick =
      isFiniteNum(l1Ask) && isFiniteNum(l1Bid) && Math.abs(l1Ask - l1Bid) > 0
        ? Math.abs(l1Ask - l1Bid)
        : isFiniteNum(realMid)
        ? Math.abs((realMid as number) * 1e-5)
        : 1;

    return (price: number) => {
      if (!isFiniteNum(visualMid) || !isFiniteNum(price) || tick <= 0) return 50;
      const rowsDelta = (price - (visualMid as number)) / tick;
      return 50 - rowsDelta * rowPct;
    };
  }, [visualMid, l1Ask, l1Bid, realMid, rowPct]);

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label="Depth glass"
      className={cx(
        "relative h-full w-full overflow-hidden rounded-xl border border-zinc-700/60 bg-zinc-900/50 p-2",
        "select-none",
        className
      )}
    >
      {/* left axis */}
      <div className="absolute left-[6px] top-0 z-[10] h-full w-px bg-zinc-700/30" />

      {/* ASKS (above center) */}
      {showAsks.map(([price, qty], i) => {
        const y = 50 - (i + 0.5) * rowPct;
        const wVsGlass = barWidthVsGlass(qty); // %
        return (
          <div
            key={`ask:${i}:${price}:${qty}`}
            className="absolute left-0 right-0 z-[20]"
            style={{ top: `${y}%` }}
            onClick={() => handlePick(price)}
            title={`Ask ${fmt(price)} · ${qty}`}
          >
            <div className="relative -translate-y-1/2">
              {/* Price label */}
              <div
                className="absolute top-1/2 -translate-y-1/2 text-[10px] font-mono text-rose-300/80"
                style={{ left: 0, width: LABEL_W_PX }}
              >
                <span className="inline-block w-full truncate pr-1">{fmt(price)}</span>
              </div>

              {/* Volume badge at the START of the bar (right after price) */}
              <div
                className="absolute top-1/2 -translate-y-1/2 text-[10px] font-mono text-rose-200/90"
                style={{ left: LABEL_W_PX + 4 }}
              >
                {qty.toFixed(2)}
              </div>

              {/* Bar lane */}
              <div className="h-2.5" style={{ marginLeft: LABEL_W_PX }}>
                <div
                  className="h-2.5 rounded-sm bg-rose-600/60"
                  style={{ width: `${wVsGlass}%` }}
                />
              </div>
            </div>
          </div>
        );
      })}

      {/* MID */}
      {isFiniteNum(visualMid) && (
        <div
          className="absolute left-0 right-0 z-[24] cursor-pointer"
          style={{ top: "50%" }}
          onClick={() => handlePick(visualMid)}
          title={`mid ${fmt(visualMid)}`}
        >
          <div className="relative -translate-y-1/2">
            <div className="absolute left-0 right-0 border-t border-dashed border-zinc-600/50" />
            <div className="absolute left-1/2 -top-3 -translate-x-1/2 rounded bg-zinc-900/80 px-1 font-mono text-[10px] text-zinc-300">
              mid {fmt(visualMid)}
            </div>
          </div>
        </div>
      )}

      {/* BIDS (below center) */}
      {showBids.map(([price, qty], j) => {
        const y = 50 + (j + 0.5) * rowPct;
        const wVsGlass = barWidthVsGlass(qty);
        return (
          <div
            key={`bid:${j}:${price}:${qty}`}
            className="absolute left-0 right-0 z-[20]"
            style={{ top: `${y}%` }}
            onClick={() => handlePick(price)}
            title={`Bid ${fmt(price)} · ${qty}`}
          >
            <div className="relative -translate-y-1/2">
              {/* Price label */}
              <div
                className="absolute top-1/2 -translate-y-1/2 text-[10px] font-mono text-emerald-300/80"
                style={{ left: 0, width: LABEL_W_PX }}
              >
                <span className="inline-block w-full truncate pr-1">{fmt(price)}</span>
              </div>

              {/* Volume badge at the START of the bar */}
              <div
                className="absolute top-1/2 -translate-y-1/2 text-[10px] font-mono text-emerald-200/90"
                style={{ left: LABEL_W_PX + 4 }}
              >
                {qty.toFixed(2)}
              </div>

              {/* Bar lane */}
              <div className="h-2.5" style={{ marginLeft: LABEL_W_PX }}>
                <div
                  className="h-2.5 rounded-sm bg-emerald-600/60"
                  style={{ width: `${wVsGlass}%` }}
                />
              </div>
            </div>
          </div>
        );
      })}

      {/* right-side position marker */}
      {isFiniteNum(positionPrice) && (
        <div
          className="absolute right-2 z-[40] pointer-events-none"
          style={{ top: `${priceToYPct(positionPrice as number)}%` }}
          title={`Position ${fmt(positionPrice)}`}
        >
          <div className="relative -translate-y-1/2">
            <div className="absolute right-full mr-1 top-1/2 -translate-y-1/2 w-6 border-t border-amber-400/70" />
            <div className="rounded-md bg-amber-500/15 text-amber-300 border border-amber-400/50 px-2 py-0.5 text-[10px] font-mono whitespace-nowrap">
              pos {fmt(positionPrice)}
            </div>
          </div>
        </div>
      )}

      {DEBUG && (
        <div className="absolute left-2 top-2 z-[45] bg-black/55 px-1.5 py-1 font-mono text-[10px] text-zinc-200">
          <div>rows top/bot: {topCount} / {bottomCount} (total {totalRows})</div>
          <div>rowPx: {rowPx} · label: {LABEL_W_PX}px</div>
          <div>bar cap vs glass: {MAX_BAR_WIDTH_VS_GLASS}%</div>
        </div>
      )}

      {!hasData && (
        <div className="absolute inset-0 z-[50] flex items-center justify-center">
          <div className="rounded border border-zinc-700/50 bg-zinc-900/70 px-2 py-1 text-[11px] text-zinc-400">
            Waiting for quotes…
          </div>
        </div>
      )}
    </div>
  );
});
