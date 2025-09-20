// src/components/charts/DepthGlass.tsx
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import cx from "classnames";
import type { Level } from "@/types/api";

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
};

/* ───────── helpers ───────── */
const EPS = 1e-12;
const PAD_PCT = 0.03;
const SHRINK_DELAY_MS = 900;
const SHRINK_ALPHA = 0.18;
const L2_LIMIT = 16;

const isFiniteNum = (n: unknown): n is number =>
  typeof n === "number" && Number.isFinite(n);

const toTuple = (lv: unknown): Level | null => {
  if (Array.isArray(lv) && lv.length >= 2) {
    const p = Number(lv[0]);
    const q = Number(lv[1]);
    return Number.isFinite(p) && p > 0 && Number.isFinite(q) ? ([p, q] as const) : null;
  }
  return null;
};

const fmt = (n?: number, dp = 6) => (isFiniteNum(n) ? n.toFixed(dp) : "—");

/* ───────── компонент ───────── */
function DepthGlass({
  bid,
  ask,
  bids = [],
  asks = [],
  positionPrice,
  tape = [],
  className,
}: Props) {
  // 1) нормализация входа
  const bidLevels = useMemo<Level[]>(
    () => (bids ?? []).slice(0, L2_LIMIT).map(toTuple).filter((x): x is Level => !!x),
    [bids]
  );
  const askLevels = useMemo<Level[]>(
    () => (asks ?? []).slice(0, L2_LIMIT).map(toTuple).filter((x): x is Level => !!x),
    [asks]
  );

  // 2) L1 и mid
  const l1Bid = isFiniteNum(bid) && bid > 0 ? bid : undefined;
  const l1Ask = isFiniteNum(ask) && ask > 0 ? ask : undefined;
  const mid =
    l1Bid != null && l1Ask != null
      ? (l1Bid + l1Ask) / 2
      : tape.length
      ? tape[tape.length - 1].mid
      : undefined;

  const hasBid = l1Bid != null;
  const hasAsk = l1Ask != null;

  const noData =
    !hasBid && !hasAsk && tape.length === 0 && bidLevels.length === 0 && askLevels.length === 0;

  // 3) Домен
  const targetDomain = useMemo(() => {
    const values: number[] = [];
    if (hasBid) values.push(l1Bid as number);
    if (hasAsk) values.push(l1Ask as number);
    for (const [p] of bidLevels) values.push(p);
    for (const [p] of askLevels) values.push(p);

    // fallback: вокруг mid или stub
    if (!values.length) {
      if (isFiniteNum(mid)) {
        const pad = mid * 0.001; // ±0.1%
        return { min: mid - pad, max: mid + pad };
      }
      return { min: 0.999999, max: 1.000001 };
    }

    const minV = Math.min(...values);
    const maxV = Math.max(...values);
    const pad = Math.max(PAD_PCT * (maxV - minV), 1e-9);

    return { min: minV - pad, max: maxV + pad };
  }, [hasBid, hasAsk, l1Bid, l1Ask, bidLevels, askLevels, mid]);

  // 4) Гистерезис домена
  const [yDomain, setYDomain] = useState(targetDomain);
  const domainRef = useRef(yDomain);
  const shrinkRef = useRef<number | null>(null);

  const setDomain = useCallback((next: { min: number; max: number }) => {
    const cur = domainRef.current;
    if (Math.abs(next.min - cur.min) > 1e-9 || Math.abs(next.max - cur.max) > 1e-9) {
      domainRef.current = next;
      setYDomain(next);
    }
  }, []);

  useEffect(() => {
    const cur = domainRef.current;
    const tgt = targetDomain;

    // Первый раз или расширение
    const needExpand = tgt.min < cur.min - EPS || tgt.max > cur.max + EPS;
    const isInitialStub = cur.min === 0.999999 && cur.max === 1.000001;

    if (needExpand || isInitialStub) {
      setDomain(tgt);
      if (shrinkRef.current) {
        window.clearTimeout(shrinkRef.current);
        shrinkRef.current = null;
      }
      return;
    }

    if (shrinkRef.current) {
      window.clearTimeout(shrinkRef.current);
      shrinkRef.current = null;
    }
    shrinkRef.current = window.setTimeout(() => {
      const c = domainRef.current;
      setDomain({
        min: c.min + (tgt.min - c.min) * SHRINK_ALPHA,
        max: c.max + (tgt.max - c.max) * SHRINK_ALPHA,
      });
      shrinkRef.current = null;
    }, SHRINK_DELAY_MS);

    return () => {
      if (shrinkRef.current) {
        window.clearTimeout(shrinkRef.current);
        shrinkRef.current = null;
      }
    };
  }, [targetDomain, setDomain]);

  // 5) scaleY
  const scaleY = useCallback(
    (price: number) => {
      const { min, max } = yDomain;
      const t = (price - min) / (max - min || 1);
      const clamped = Math.max(0, Math.min(1, t));
      return (1 - clamped) * 100;
    },
    [yDomain]
  );

  // 6) Кумулятивные суммы
  const { bidCum, askCum, cumMax } = useMemo(() => {
    const b: Array<{ price: number; qty: number; cum: number }> = [];
    const a: Array<{ price: number; qty: number; cum: number }> = [];
    let acc = 0;
    for (const [p, q] of bidLevels) {
      acc += q;
      b.push({ price: p, qty: q, cum: acc });
    }
    acc = 0;
    for (const [p, q] of askLevels) {
      acc += q;
      a.push({ price: p, qty: q, cum: acc });
    }
    const lastB = b.length ? b[b.length - 1].cum : 0;
    const lastA = a.length ? a[a.length - 1].cum : 0;
    return { bidCum: b, askCum: a, cumMax: Math.max(lastB, lastA, 1) };
  }, [bidLevels, askLevels]);

  const widthPct = (cum: number) => {
    const t = Math.sqrt(Math.max(0, cum) / Math.max(1, cumMax));
    return Math.max(4, Math.round(t * 48));
  };

  return (
    <div
      role="img"
      aria-label="Depth glass"
      className={cx(
        "relative rounded-xl border border-zinc-700/60 bg-zinc-900/50 overflow-hidden p-2 h-full",
        className
      )}
    >
      {/* Центральная ось */}
      <div className="absolute left-1/2 top-0 h-full w-px bg-zinc-700/40 z-[12]" />

      {/* Биды */}
      {bidCum.map(({ price, qty, cum }) => (
        <div key={`b:${price}:${qty}`} className="absolute left-0 right-0 z-[20]" style={{ top: `${scaleY(price)}%` }}>
          <div className="relative -translate-y-1/2">
            <div className="absolute right-1/2 mr-2 flex items-center gap-1">
              <div
                className="h-2.5 rounded-r bg-emerald-600/60"
                style={{ width: `${widthPct(cum)}%` }}
              />
              <span className="text-[10px] text-emerald-300/80 font-mono">{fmt(price)}</span>
              <span className="text-[10px] text-emerald-400/70 font-mono">{qty.toFixed(2)}</span>
            </div>
          </div>
        </div>
      ))}

      {/* Аски */}
      {askCum.map(({ price, qty, cum }) => (
        <div key={`a:${price}:${qty}`} className="absolute left-0 right-0 z-[20]" style={{ top: `${scaleY(price)}%` }}>
          <div className="relative -translate-y-1/2">
            <div className="absolute left-1/2 ml-2 flex items-center gap-1">
              <span className="text-[10px] text-rose-300/80 font-mono">{fmt(price)}</span>
              <span className="text-[10px] text-rose-400/70 font-mono">{qty.toFixed(2)}</span>
              <div
                className="h-2.5 rounded-l bg-rose-600/60"
                style={{ width: `${widthPct(cum)}%` }}
              />
            </div>
          </div>
        </div>
      ))}

      {/* Mid */}
      {isFiniteNum(mid) && (
        <div className="absolute left-0 right-0 z-[24]" style={{ top: `${scaleY(mid)}%` }}>
          <div className="relative -translate-y-1/2">
            <div className="absolute left-0 right-0 border-t border-dashed border-zinc-600/50" />
            <div className="absolute left-1/2 -translate-x-1/2 -top-3 text-[10px] text-zinc-300 font-mono bg-zinc-900/80 px-1 rounded">
              mid {fmt(mid)}
            </div>
          </div>
        </div>
      )}

      {/* Позиция */}
      {isFiniteNum(positionPrice) && (
        <div className="absolute left-0 right-0 z-[34]" style={{ top: `${scaleY(positionPrice)}%` }}>
          <div className="relative -translate-y-1/2 text-amber-300 font-mono text-[10px]">
            Pos {fmt(positionPrice)}
          </div>
        </div>
      )}

      {/* DEBUG */}
      {DEBUG && (
        <div className="absolute left-2 top-2 z-[45] bg-black/55 px-1.5 py-1 text-[10px] font-mono text-zinc-200">
          <div>L2: b{bidLevels.length}/a{askLevels.length}</div>
          <div>mid: {fmt(mid)}</div>
          <div>dom: [{fmt(yDomain.min)}; {fmt(yDomain.max)}]</div>
        </div>
      )}

      {/* Нет данных */}
      {noData && (
        <div className="absolute inset-0 flex items-center justify-center z-[50]">
          <div className="px-2 py-1 text-[11px] text-zinc-400 bg-zinc-900/70 rounded border border-zinc-700/50">
            Waiting for quotes…
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(DepthGlass);
