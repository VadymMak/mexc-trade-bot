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

type SessionFilter = 'all' | 'asia' | 'europe' | 'overlap' | 'us' | 'quiet';

interface DisplayRow {
  symbol: string;
  exchange: string;
  bid: number;
  ask: number;
  spreadBps: number;
  volumeUsd: number;
  score: number | undefined;
  tradingSession?: string | null;
  brainVerdict?: string | null;
  brainWinRate?: number | null;
}

/* ─────────────────── Helpers ─────────────────── */

function getCurrentSession(): SessionFilter {
  const h = new Date().getUTCHours();
  if (h < 8) return 'asia';
  if (h < 13) return 'europe';
  if (h < 16) return 'overlap';
  if (h < 22) return 'us';
  return 'quiet';
}

function getRowSession(row: DisplayRow): SessionFilter {
  const s = (row.tradingSession ?? '').toLowerCase();
  if (s === 'asia' || s === 'europe' || s === 'overlap' || s === 'us' || s === 'quiet') {
    return s as SessionFilter;
  }
  return getCurrentSession();
}

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
      tradingSession: row.trading_session ?? null,
      brainVerdict: row.brain_verdict ?? null,
      brainWinRate: row.brain_win_rate ?? null,
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
    tradingSession: row.trading_session ?? null,
    brainVerdict: row.brain_verdict ?? null,
    brainWinRate: row.brain_win_rate ?? null,
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

function BrainBadge({ verdict }: { verdict: string | null | undefined }) {
  if (verdict === 'strong_entry') {
    return <span className={`${styles.badge} ${styles.badgeBrainGreen}`}>↑ STRONG</span>;
  }
  if (verdict === 'avoid') {
    return <span className={`${styles.badge} ${styles.badgeBrainRed}`}>↓ AVOID</span>;
  }
  return <span className={styles.volume}>—</span>;
}

/* ─────────────────── Component ─────────────────── */

export default function SpreadTable({ search, minSpreadPct }: SpreadTableProps) {
  const [rows, setRows] = useState<DisplayRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [session, setSession] = useState<SessionFilter>('all');

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

  usePolling(fetchData, 15_000);

  // Filter
  const minBps = minSpreadPct * 100;
  const filtered = rows.filter((r) => {
    const matchSearch = !search || r.symbol.toUpperCase().includes(search.toUpperCase());
    const matchSpread = r.spreadBps >= minBps;
    const matchSession = session === 'all' || getRowSession(r) === session;
    return matchSearch && matchSpread && matchSession;
  });

  // Session dropdown is always rendered; table content is conditional
  return (
    <div className={styles.wrapper}>
      <div className={styles.filterBar}>
        <span className={styles.filterLabel}>Session</span>
        <select
          className={styles.select}
          value={session}
          onChange={(e) => setSession(e.target.value as SessionFilter)}
        >
          <option value="all">All</option>
          <option value="asia">Asia (0–8 UTC)</option>
          <option value="europe">Europe (8–13 UTC)</option>
          <option value="overlap">Overlap (13–16 UTC)</option>
          <option value="us">US (16–22 UTC)</option>
          <option value="quiet">Quiet (22–24 UTC)</option>
        </select>
      </div>

      {loading && (
        <div className={styles.status}>
          <span className={styles.spinner} />
          Loading scanner data…
        </div>
      )}

      {!loading && error && (
        <div className={`${styles.status} ${styles.error}`}>Error: {error}</div>
      )}

      {!loading && !error && filtered.length === 0 && (
        <div className={styles.status}>No symbols match current filters.</div>
      )}

      {!loading && !error && filtered.length > 0 && (
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
              <th>Brain</th>
              <th>Win%</th>
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
                    <BrainBadge verdict={row.brainVerdict} />
                  </td>
                  <td className={styles.cell}>
                    <span className={styles.volume}>
                      {row.brainWinRate != null
                        ? `${(row.brainWinRate * 100).toFixed(0)}%`
                        : '—'}
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
      )}

      {lastUpdated && (
        <div className={styles.lastUpdated}>
          Updated {new Date(lastUpdated).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
