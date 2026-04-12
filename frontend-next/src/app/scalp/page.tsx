'use client';

import { useState, useCallback } from 'react';
import { usePolling } from '@/hooks/usePolling';
import styles from './page.module.css';

/* ─────────────────── Types ─────────────────── */

type ScalpTab = 'stats' | 'positions';

interface ScalpStats {
  open_count:    number;
  closed_count:  number;
  win_count:     number;
  tp_rate:       number;
  total_net_pnl: number;
  avg_net_pnl:   number;
  avg_hold_sec:  number;
  session?: {
    open_scalp:    number;
    total_opened:  number;
    total_closed:  number;
    total_net_pnl: number;
  };
  last_updated?: string;
}

interface ScalpPosition {
  id:              number;
  symbol:          string;
  exchange:        string;
  direction:       'LONG' | 'SHORT';
  status:          'open' | 'closed';
  entry_price:     number;
  exit_price:      number | null;
  opened_at:       string;
  closed_at:       string | null;
  hold_seconds:    number | null;
  exit_reason:     string | null;
  gross_pnl_usdt:  number | null;
  net_pnl_usdt:    number | null;
  mm_repeat_score: number | null;
  buy_pressure:    number | null;
  trade_velocity:  number | null;
}

/* ─────────────────── Helpers ─────────────────── */

function fmtHold(sec: number | null): string {
  if (sec == null) return '—';
  if (sec < 60)   return `${sec}s`;
  return `${Math.round(sec / 60)}m`;
}

function fmtPrice(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1000) return `$${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
  if (v >= 1)    return `$${v.toFixed(4)}`;
  return `$${v.toFixed(6)}`;
}

function pnlClass(v: number | null): string {
  if (v == null) return '';
  return v >= 0 ? styles.pnlPos : styles.pnlNeg;
}

function fmtPnl(v: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}$${v.toFixed(4)}`;
}

/* ─────────────────── Direction badge ─────────────────── */

function DirBadge({ dir }: { dir: 'LONG' | 'SHORT' }) {
  return (
    <span className={`${styles.badge} ${dir === 'LONG' ? styles.badgeGreen : styles.badgeRed}`}>
      {dir}
    </span>
  );
}

/* ─────────────────── Exit reason badge ─────────────────── */

function ExitBadge({ reason }: { reason: string | null }) {
  if (!reason) return <span className={styles.muted}>—</span>;
  const cls =
    reason === 'TAKE_PROFIT' ? styles.badgeGreen
    : reason === 'STOP_LOSS' ? styles.badgeRed
    : reason === 'TIMEOUT'   ? styles.badgeYellow
    : styles.badgeGray;
  const label =
    reason === 'TAKE_PROFIT' ? '✅ TP'
    : reason === 'STOP_LOSS' ? '❌ SL'
    : reason === 'TIMEOUT'   ? '⏱ TIMEOUT'
    : reason;
  return <span className={`${styles.badge} ${cls}`}>{label}</span>;
}

/* ─────────────────── Stats panel ─────────────────── */

function StatsPanel({ stats }: { stats: ScalpStats | null }) {
  if (!stats) {
    return (
      <div className={styles.statsGrid}>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Status</div>
          <div className={`${styles.statValue} ${styles.muted}`} style={{ fontSize: '14px' }}>
            Waiting for first 60s push…
          </div>
        </div>
      </div>
    );
  }

  const pnlPos = stats.total_net_pnl >= 0;

  return (
    <div>
      <div className={styles.statsGrid}>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Open</div>
          <div className={styles.statValue}>{stats.open_count}</div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Closed</div>
          <div className={styles.statValue}>{stats.closed_count}</div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>TP Rate</div>
          <div className={`${styles.statValue} ${stats.tp_rate >= 0.5 ? styles.pnlPos : styles.pnlNeg}`}>
            {stats.closed_count > 0 ? `${(stats.tp_rate * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Net PnL</div>
          <div className={`${styles.statValue} ${pnlPos ? styles.pnlPos : styles.pnlNeg}`}>
            {pnlPos ? '+' : ''}${stats.total_net_pnl.toFixed(4)}
          </div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Avg PnL</div>
          <div className={`${styles.statValue} ${stats.avg_net_pnl >= 0 ? styles.pnlPos : styles.pnlNeg}`}
               style={{ fontSize: '16px' }}>
            {stats.closed_count > 0 ? fmtPnl(stats.avg_net_pnl) : '—'}
          </div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Avg Hold</div>
          <div className={styles.statValue} style={{ fontSize: '16px' }}>
            {fmtHold(stats.avg_hold_sec)}
          </div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Wins</div>
          <div className={`${styles.statValue} ${styles.pnlPos}`} style={{ fontSize: '16px' }}>
            {stats.win_count}
          </div>
        </div>
      </div>

      {stats.session && (
        <div className={styles.statsUpdated} style={{ marginTop: '8px', fontSize: '12px', color: 'var(--color-text-muted)' }}>
          Session: opened {stats.session.total_opened} · closed {stats.session.total_closed} · net{' '}
          <span className={stats.session.total_net_pnl >= 0 ? styles.pnlPos : styles.pnlNeg}>
            {stats.session.total_net_pnl >= 0 ? '+' : ''}${stats.session.total_net_pnl.toFixed(4)}
          </span>
        </div>
      )}

      {stats.last_updated && (
        <div className={styles.statsUpdated}>
          Updated: {new Date(stats.last_updated).toLocaleTimeString()}
        </div>
      )}

      <a
        href="https://mexc-trade-bot-production.up.railway.app/api/scalp/positions"
        target="_blank"
        rel="noreferrer"
        className={styles.csvBtn}
      >
        🔗 Raw JSON
      </a>
    </div>
  );
}

/* ─────────────────── Positions tab ─────────────────── */

function PositionsTab({
  status,
  label,
}: {
  status: 'open' | 'closed' | undefined;
  label: string;
}) {
  const [positions, setPositions] = useState<ScalpPosition[]>([]);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const fetchPositions = useCallback(async () => {
    const url = status
      ? `/api/proxy/api/scalp/positions?status=${status}&limit=200`
      : '/api/proxy/api/scalp/positions?limit=200';
    const r = await fetch(url);
    if (!r.ok) return;
    const data = await r.json() as ScalpPosition[];
    setPositions(data);
    setUpdatedAt(new Date().toLocaleTimeString());
  }, [status]);

  usePolling(fetchPositions, 15_000);

  if (positions.length === 0) {
    return (
      <div>
        <div className={styles.sseIndicator} style={{ marginBottom: '12px' }}>
          <span className={updatedAt ? styles.sseDot : styles.sseDotOff} />
          {updatedAt ? `Updated ${updatedAt}` : 'Loading…'}
        </div>
        <div className={styles.emptyState}>{label}</div>
      </div>
    );
  }

  return (
    <div>
      <div className={styles.sseIndicator} style={{ marginBottom: '12px' }}>
        <span className={styles.sseDot} />
        Updated {updatedAt} · {positions.length} rows
      </div>
      <div className={styles.tableCard}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>Symbol</th>
              <th className={styles.th}>Dir</th>
              <th className={styles.th}>Entry</th>
              <th className={styles.th}>Exit</th>
              <th className={styles.th}>Hold</th>
              <th className={styles.th}>Reason</th>
              <th className={styles.th}>Net PnL</th>
              <th className={styles.th}>MM Score</th>
              <th className={styles.th}>BP</th>
              <th className={styles.th}>Velocity</th>
              <th className={styles.th}>Opened At</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((pos) => (
              <tr key={pos.id} className={styles.row}>
                <td className={styles.td}>
                  <span className={styles.symbol}>{pos.symbol}</span>
                </td>
                <td className={styles.td}>
                  <DirBadge dir={pos.direction} />
                </td>
                <td className={styles.td}>{fmtPrice(pos.entry_price)}</td>
                <td className={styles.td}>{fmtPrice(pos.exit_price)}</td>
                <td className={styles.td}>{fmtHold(pos.hold_seconds)}</td>
                <td className={styles.td}>
                  <ExitBadge reason={pos.exit_reason} />
                </td>
                <td className={styles.td}>
                  <span className={pnlClass(pos.net_pnl_usdt)}>
                    {fmtPnl(pos.net_pnl_usdt)}
                  </span>
                </td>
                <td className={styles.td}>
                  <span className={styles.muted}>
                    {pos.mm_repeat_score != null ? pos.mm_repeat_score.toFixed(2) : '—'}
                  </span>
                </td>
                <td className={styles.td}>
                  <span className={styles.muted}>
                    {pos.buy_pressure != null ? pos.buy_pressure.toFixed(2) : '—'}
                  </span>
                </td>
                <td className={styles.td}>
                  <span className={styles.muted}>
                    {pos.trade_velocity != null ? pos.trade_velocity.toFixed(0) : '—'}
                  </span>
                </td>
                <td className={styles.td}>
                  <span className={styles.muted}>
                    {pos.opened_at ? new Date(pos.opened_at).toLocaleTimeString() : '—'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─────────────────── Page ─────────────────── */

export default function ScalpPage() {
  const [tab, setTab] = useState<ScalpTab>('stats');
  const [stats, setStats] = useState<ScalpStats | null>(null);

  const fetchStats = useCallback(async () => {
    try {
      const r = await fetch('/api/proxy/api/scalp/stats');
      if (!r.ok) return;
      const data = await r.json() as ScalpStats;
      setStats(data);
    } catch { /* ignore */ }
  }, []);

  usePolling(fetchStats, 60_000);

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>⚡ Scalp Simulator</h1>
        <p className={styles.subtitle}>
          MEXC-only directional scalping · mm_repeat_score + buy_pressure · TP 0.15% / SL 0.20%
        </p>
      </div>

      {/* Always-visible stats row */}
      <StatsPanel stats={stats} />

      {/* Tab bar */}
      <div className={styles.tabBar}>
        <button
          className={`${styles.tab} ${tab === 'stats'     ? styles.tabActive : ''}`}
          onClick={() => setTab('stats')}
        >
          📊 Stats
        </button>
        <button
          className={`${styles.tab} ${tab === 'positions' ? styles.tabActive : ''}`}
          onClick={() => setTab('positions')}
        >
          📋 All Positions
        </button>
      </div>

      <div className={styles.content}>
        {tab === 'stats' && (
          <div>
            <p className={styles.muted} style={{ padding: '16px 0' }}>
              Strategy: detect active Market Maker robot (mm_repeat_score ≥ 0.50) with
              directional flow (buy_pressure ≥ 0.65 → LONG, ≤ 0.35 → SHORT) and active
              market (trade_velocity ≥ 10 trades/min). Exit at TP +0.15%, SL −0.20%, or
              5-minute timeout. Fee: 0.02% per side (0.04% round-trip). Paper size $10.
            </p>
            <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--color-text)', marginBottom: '12px' }}>
              Open Positions
            </h3>
            <PositionsTab status="open" label="No open scalp positions." />
          </div>
        )}
        {tab === 'positions' && (
          <PositionsTab status={undefined} label="No scalp positions recorded yet." />
        )}
      </div>
    </div>
  );
}
