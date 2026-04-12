'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import Link from 'next/link';
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

const POLL_MS = 30_000; // 30 seconds for everything

/* ─────────────────── Helpers ─────────────────── */

function fmtHold(sec: number | null): string {
  if (sec == null) return '—';
  const s = Math.round(sec);
  if (s < 60)   return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** Live hold for open positions: seconds since opened_at */
function liveHold(openedAt: string): string {
  const secs = Math.max(0, Math.floor((Date.now() - new Date(openedAt).getTime()) / 1000));
  return fmtHold(secs);
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

/* ─────────────────── Countdown ─────────────────── */

function RefreshCountdown({ updatedAt }: { updatedAt: string | null }) {
  const [secs, setSecs] = useState(POLL_MS / 1000);

  useEffect(() => {
    setSecs(POLL_MS / 1000);
    const iv = setInterval(() => setSecs(s => Math.max(0, s - 1)), 1000);
    return () => clearInterval(iv);
  }, [updatedAt]);

  return (
    <span className={styles.sseIndicator}>
      <span className={updatedAt ? styles.sseDot : styles.sseDotOff} />
      {updatedAt ? `Updated ${updatedAt}` : 'Loading…'}
      {updatedAt && (
        <span style={{ opacity: 0.5, marginLeft: 4 }}>· refresh in {secs}s</span>
      )}
    </span>
  );
}

/* ─────────────────── Badges ─────────────────── */

function DirBadge({ dir }: { dir: 'LONG' | 'SHORT' }) {
  return (
    <span className={`${styles.badge} ${dir === 'LONG' ? styles.badgeGreen : styles.badgeRed}`}>
      {dir}
    </span>
  );
}

function StatusBadge({ status }: { status: 'open' | 'closed' }) {
  return (
    <span className={`${styles.badge} ${status === 'open' ? styles.badgeBlue : styles.badgeGray}`}>
      {status === 'open' ? '⚡ OPEN' : 'CLOSED'}
    </span>
  );
}

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

function StatsPanel({ stats, updatedAt }: { stats: ScalpStats | null; updatedAt: string | null }) {
  if (!stats) {
    return (
      <div className={styles.statsGrid}>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Status</div>
          <div className={`${styles.statValue} ${styles.muted}`} style={{ fontSize: '14px' }}>
            Waiting for first push…
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

      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '12px', alignItems: 'center' }}>
        <RefreshCountdown updatedAt={updatedAt} />
        <a
          href="https://mexc-trade-bot-production.up.railway.app/api/scalp/export-dataset"
          download
          className={styles.csvBtn}
        >
          ⬇ Download CSV
        </a>
        <Link href="/scalp/analyzer" className={styles.csvBtn} style={{ textDecoration: 'none', display: 'inline-block' }}>
          📊 Analyzer →
        </Link>
      </div>
    </div>
  );
}

/* ─────────────────── Positions table ─────────────────── */

function PositionsTable({
  positions,
  updatedAt,
  emptyLabel,
}: {
  positions: ScalpPosition[];
  updatedAt: string | null;
  emptyLabel: string;
}) {
  // Live clock for open hold times
  const [, setTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTick(t => t + 1), 5000);
    return () => clearInterval(iv);
  }, []);

  return (
    <div>
      <div className={styles.sseIndicator} style={{ marginBottom: '12px' }}>
        <span className={updatedAt ? styles.sseDot : styles.sseDotOff} />
        {updatedAt ? `Updated ${updatedAt} · ${positions.length} rows` : 'Loading…'}
      </div>

      {positions.length === 0 ? (
        <div className={styles.emptyState}>{emptyLabel}</div>
      ) : (
        <div className={styles.tableCard}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>Symbol</th>
                <th className={styles.th}>Status</th>
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
                <tr
                  key={pos.id}
                  className={styles.row}
                  style={pos.status === 'open' ? { background: 'rgba(96,165,250,0.04)' } : undefined}
                >
                  <td className={styles.td}>
                    <span className={styles.symbol}>{pos.symbol}</span>
                  </td>
                  <td className={styles.td}>
                    <StatusBadge status={pos.status} />
                  </td>
                  <td className={styles.td}>
                    <DirBadge dir={pos.direction} />
                  </td>
                  <td className={styles.td}>{fmtPrice(pos.entry_price)}</td>
                  <td className={styles.td}>
                    {pos.exit_price != null ? fmtPrice(pos.exit_price) : <span className={styles.muted}>—</span>}
                  </td>
                  <td className={styles.td}>
                    {pos.status === 'open'
                      ? <span style={{ color: '#60a5fa' }}>{liveHold(pos.opened_at)}</span>
                      : fmtHold(pos.hold_seconds)
                    }
                  </td>
                  <td className={styles.td}>
                    {pos.status === 'open'
                      ? <span className={styles.muted}>running…</span>
                      : <ExitBadge reason={pos.exit_reason} />
                    }
                  </td>
                  <td className={styles.td}>
                    {pos.status === 'open'
                      ? <span className={styles.muted}>—</span>
                      : <span className={pnlClass(pos.net_pnl_usdt)}>{fmtPnl(pos.net_pnl_usdt)}</span>
                    }
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
      )}
    </div>
  );
}

/* ─────────────────── Page ─────────────────── */

export default function ScalpPage() {
  const [tab, setTab] = useState<ScalpTab>('stats');
  const [stats, setStats]         = useState<ScalpStats | null>(null);
  const [statsAt, setStatsAt]     = useState<string | null>(null);
  const [positions, setPositions] = useState<ScalpPosition[]>([]);
  const [posAt, setPosAt]         = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    try {
      const r = await fetch('/api/proxy/api/scalp/stats');
      if (!r.ok) return;
      const data = await r.json() as ScalpStats;
      setStats(data);
      setStatsAt(new Date().toLocaleTimeString());
    } catch { /* ignore */ }
  }, []);

  const fetchPositions = useCallback(async () => {
    try {
      const r = await fetch('/api/proxy/api/scalp/positions?limit=300');
      if (!r.ok) return;
      const data = await r.json() as ScalpPosition[];
      setPositions(data);
      setPosAt(new Date().toLocaleTimeString());
    } catch { /* ignore */ }
  }, []);

  // Both poll every 30s
  usePolling(fetchStats,     POLL_MS);
  usePolling(fetchPositions, POLL_MS);

  const openPositions   = positions.filter(p => p.status === 'open');
  const closedPositions = positions.filter(p => p.status === 'closed');

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>⚡ Scalp Simulator</h1>
        <p className={styles.subtitle}>
          MEXC-only directional scalping · mm_repeat_score ≥ 0.60 + buy_pressure · TP 0.30% / SL 0.20% · auto-refresh 30s
        </p>
      </div>

      {/* Stats panel */}
      <StatsPanel stats={stats} updatedAt={statsAt} />

      {/* Tab bar */}
      <div className={styles.tabBar}>
        <button
          className={`${styles.tab} ${tab === 'stats'     ? styles.tabActive : ''}`}
          onClick={() => setTab('stats')}
        >
          📊 Stats
          {openPositions.length > 0 && (
            <span className={styles.tabCount}>{openPositions.length}</span>
          )}
        </button>
        <button
          className={`${styles.tab} ${tab === 'positions' ? styles.tabActive : ''}`}
          onClick={() => setTab('positions')}
        >
          📋 All Positions
          {positions.length > 0 && (
            <span className={styles.tabCount}>{positions.length}</span>
          )}
        </button>
      </div>

      <div className={styles.content}>
        {tab === 'stats' && (
          <div>
            <p className={styles.muted} style={{ padding: '12px 0' }}>
              Strategy: detect active MM robot (mm_repeat_score ≥ 0.60) with
              directional flow (buy_pressure ≥ 0.65 → LONG, ≤ 0.35 → SHORT) and active
              market (trade_velocity ≥ 10 trades/min). Exit at TP +0.30%, SL −0.20%, or
              5-minute timeout. Fee: 0.02% per side (0.04% round-trip). Paper size $10.
            </p>
            <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--color-text)', marginBottom: '10px' }}>
              ⚡ Open Positions ({openPositions.length})
            </h3>
            <PositionsTable
              positions={openPositions}
              updatedAt={posAt}
              emptyLabel="No open scalp positions."
            />
          </div>
        )}
        {tab === 'positions' && (
          <div>
            {closedPositions.length > 0 && (
              <p className={styles.muted} style={{ padding: '8px 0', fontSize: '12px' }}>
                Showing {positions.length} total · {openPositions.length} open · {closedPositions.length} closed
              </p>
            )}
            <PositionsTable
              positions={positions}
              updatedAt={posAt}
              emptyLabel="No scalp positions recorded yet."
            />
          </div>
        )}
      </div>
    </div>
  );
}
