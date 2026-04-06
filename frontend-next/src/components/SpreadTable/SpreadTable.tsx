'use client';

import { useState, useCallback } from 'react';
import { usePolling } from '@/hooks/usePolling';
import type { ScannerRow, FeatureSnapshot, ScannerTopTieredResponse } from '@/types/scanner';
import styles from './SpreadTable.module.css';

/* ─────────────────── Types ─────────────────── */

interface SpreadTableProps {
  search: string;
  minSpreadPct: number;
}

interface DisplayRow {
  symbol: string;
  exchange: string;
  bid: number;
  ask: number;
  spreadBps: number;
  volumeUsd: number;
  score: number | undefined;
}

/* ─────────────────── Helpers ─────────────────── */

function toDisplayRow(row: ScannerRow | FeatureSnapshot): DisplayRow {
  if ('metrics' in row) {
    // FeatureSnapshot from top_tiered
    const mid = row.metrics.effective_spread_bps ?? 0;
    return {
      symbol: row.symbol,
      exchange: row.venue,
      bid: 0,
      ask: 0,
      spreadBps: mid,
      volumeUsd: row.metrics.usd_per_min ?? 0,
      score: row.score,
    };
  }
  // ScannerRow from /top
  const bid = row.bid ?? 0;
  const ask = row.ask ?? 0;
  const spreadBps =
    row.spread_bps ??
    (bid > 0 && ask > 0 ? ((ask - bid) / ((ask + bid) / 2)) * 10000 : 0);
  return {
    symbol: row.symbol,
    exchange: row.exchange ?? 'mexc',
    bid,
    ask,
    spreadBps,
    volumeUsd: row.usd_per_min ?? 0,
    score: row.score,
  };
}

function getSignal(spreadBps: number): 'ENTER' | 'WATCH' | 'SKIP' | null {
  const pct = spreadBps / 100;
  if (pct > 2) return 'ENTER';
  if (pct > 0.5) return 'WATCH';
  if (spreadBps < 0) return 'SKIP';
  return null;
}

function fmtPrice(v: number): string {
  if (v === 0) return '—';
  return v >= 1 ? v.toFixed(4) : v.toPrecision(5);
}

function fmtVol(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function fmtSpread(bps: number): string {
  return `${(bps / 100).toFixed(3)}%`;
}

/* ─────────────────── Component ─────────────────── */

export default function SpreadTable({ search, minSpreadPct }: SpreadTableProps) {
  const [rows, setRows] = useState<DisplayRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        preset: 'hedgehog',
        quote: 'USDT',
        limit: '40',
        fetch_candles: 'false',
        depth_bps_levels: '5,10',
        explain: 'false',
      });

      const r = await fetch(`/api/proxy/api/scanner/mexc/top_tiered?${params.toString()}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);

      const data: ScannerTopTieredResponse = await r.json();
      const all = [...(data.tierA ?? []), ...(data.tierB ?? [])];
      setRows(all.map(toDisplayRow));
      setLastUpdated(Date.now());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }, []);

  usePolling(fetchData, 2_000);

  // Filter
  const minBps = minSpreadPct * 100;
  const filtered = rows.filter((r) => {
    const matchSearch =
      !search || r.symbol.toUpperCase().includes(search.toUpperCase());
    const matchSpread = r.spreadBps >= minBps;
    return matchSearch && matchSpread;
  });

  if (loading) {
    return (
      <div className={styles.status}>
        <span className={styles.spinner} />
        Loading scanner data…
      </div>
    );
  }

  if (error) {
    return <div className={`${styles.status} ${styles.error}`}>Error: {error}</div>;
  }

  if (filtered.length === 0) {
    return (
      <div className={styles.status}>
        No symbols match current filters.
      </div>
    );
  }

  return (
    <div className={styles.wrapper}>
      <table className={styles.table}>
        <thead className={styles.thead}>
          <tr>
            <th>Symbol</th>
            <th>Exchange</th>
            <th>Bid</th>
            <th>Ask</th>
            <th>Spread</th>
            <th>Vol/min</th>
            <th>Score</th>
            <th>Signal</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((row) => {
            const signal = getSignal(row.spreadBps);
            const rowClass =
              row.spreadBps > 200
                ? styles.rowHot
                : row.spreadBps < 0
                ? styles.rowNegative
                : styles.rowNeutral;
            const spreadClass =
              row.spreadBps > 0
                ? styles.spreadPositive
                : row.spreadBps < 0
                ? styles.spreadNegative
                : styles.spreadNeutral;

            return (
              <tr key={`${row.exchange}-${row.symbol}`} className={`${styles.row} ${rowClass}`}>
                <td className={styles.cell}>
                  <span className={styles.symbol}>{row.symbol}</span>
                </td>
                <td className={styles.cell}>
                  <span className={styles.exchange}>{row.exchange}</span>
                </td>
                <td className={styles.cell}>
                  <span className={styles.price}>{fmtPrice(row.bid)}</span>
                </td>
                <td className={styles.cell}>
                  <span className={styles.price}>{fmtPrice(row.ask)}</span>
                </td>
                <td className={styles.cell}>
                  <span className={spreadClass}>{fmtSpread(row.spreadBps)}</span>
                </td>
                <td className={styles.cell}>
                  <span className={styles.volume}>{fmtVol(row.volumeUsd)}</span>
                </td>
                <td className={styles.cell}>
                  <span className={styles.volume}>
                    {row.score != null ? row.score.toFixed(0) : '—'}
                  </span>
                </td>
                <td className={styles.cell}>
                  {signal === 'ENTER' && (
                    <span className={`${styles.badge} ${styles.badgeEnter}`}>ENTER</span>
                  )}
                  {signal === 'WATCH' && (
                    <span className={`${styles.badge} ${styles.badgeWatch}`}>WATCH</span>
                  )}
                  {signal === 'SKIP' && (
                    <span className={`${styles.badge} ${styles.badgeSkip}`}>SKIP</span>
                  )}
                  {signal === null && <span className={styles.volume}>—</span>}
                </td>
                <td className={`${styles.cell} ${styles.actionCell}`}>
                  <button
                    className={styles.tradeBtn}
                    onClick={() => console.log('[Trade]', row.symbol, row.exchange)}
                  >
                    Trade
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {lastUpdated && (
        <div className={styles.lastUpdated}>
          Updated {new Date(lastUpdated).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
