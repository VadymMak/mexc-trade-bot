'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { usePolling } from '@/hooks/usePolling';
import styles from './page.module.css';

/* ─────────────────── Types ─────────────────── */

type Tab = 'research' | 'queue' | 'active';
type EntryModeFilter = 'all' | 'zscore' | 'large_spread';

interface ArbPair {
  symbol: string;
  exchange_long: string;
  exchange_short: string;
  price_long?: number;
  price_short?: number;
  spread_pct: number;
  zscore: number | null;
  status: 'watching' | 'signal' | 'trading';
  last_updated: string;
  entry_mode?: 'zscore' | 'large_spread' | null;
}

interface QueueItem {
  id: number;
  symbol: string;
  exchange_long: string;
  exchange_short: string;
  score: number;
  win_rate: number;
  signals_per_day: number;
  avg_hold_minutes: number;
  avg_net_pnl_usdt: number;
  total_paper_trades: number;
  days_observed: number;
  submitted_at: string;
}

interface ActivePosition {
  id: number;
  symbol: string;
  exchange_long: string;
  exchange_short: string;
  entry_spread_pct: number;
  current_spread_pct: number;
  size_usdt: number;
  opened_at: string;
  hold_minutes: number;
  unrealized_pnl_usdt: number;
  mode: 'paper';
}

interface ToastItem {
  id: number;
  message: string;
  type: 'success' | 'error';
}

interface SessionStats {
  open_positions: number;
  total_opened: number;
  total_closed: number;
  total_net_pnl: number;
  breakeven_pct: number;
}

interface PairStat {
  symbol: string;
  exchange_long: string;
  exchange_short: string;
  status: string;
  entry_spread: number;
  entry_zscore: number | null;
}

interface ResearchStats {
  session: SessionStats;
  pairs: PairStat[];
  updated_at: string | null;
}

/* ─────────────────── Helpers ─────────────────── */

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(3)}%`;
}

function fmtPrice(v: number | undefined): string {
  if (v == null) return '—';
  if (v >= 1000) return `$${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
  if (v >= 1)    return `$${v.toFixed(4)}`;
  return `$${v.toFixed(6)}`;
}

function fmtHold(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)}m`;
  return `${(minutes / 60).toFixed(1)}h`;
}

/* ─────────────────── Toast overlay ─────────────────── */

function ToastStack({ items }: { items: ToastItem[] }) {
  if (items.length === 0) return null;
  return (
    <div className={styles.toastContainer}>
      {items.map((t) => (
        <div
          key={t.id}
          className={`${styles.toast} ${t.type === 'error' ? styles.toastError : styles.toastSuccess}`}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

/* ─────────────────── Status badge ─────────────────── */

function StatusBadge({ status }: { status: ArbPair['status'] }) {
  const cls =
    status === 'trading' ? styles.badgeGreen
    : status === 'signal' ? styles.badgeYellow
    : styles.badgeGray;
  return <span className={`${styles.badge} ${cls}`}>{status}</span>;
}

/* ─────────────────── Entry mode badge ─────────────────── */

function EntryModeBadge({ mode }: { mode: ArbPair['entry_mode'] }) {
  if (mode === 'zscore') {
    return <span className={`${styles.badge} ${styles.badgeZscore}`}>ZSCORE</span>;
  }
  if (mode === 'large_spread') {
    return <span className={`${styles.badge} ${styles.badgeLargeSpread}`}>SPREAD</span>;
  }
  return <span className={styles.muted}>—</span>;
}

/* ─────────────────── Stats panel ─────────────────── */

function StatsPanel({ stats }: { stats: ResearchStats | null }) {
  if (!stats) {
    return (
      <div className={styles.statsPanel}>
        <span className={styles.muted}>Simulator stats load after first 60s report…</span>
      </div>
    );
  }

  const s = stats.session;
  const winRate = s.total_closed > 0
    ? ((stats.pairs.filter((p) => p.status === 'open').length / Math.max(s.total_opened, 1)) * 100)
    : null;
  const pnlPos = s.total_net_pnl >= 0;

  return (
    <div className={styles.statsPanel}>
      <div className={styles.statsPanelTitle}>📊 Paper Simulator</div>
      <div className={styles.statsGrid}>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Open</div>
          <div className={styles.statValue}>{s.open_positions}</div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Opened</div>
          <div className={styles.statValue}>{s.total_opened}</div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Closed</div>
          <div className={styles.statValue}>{s.total_closed}</div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Net PnL</div>
          <div className={`${styles.statValue} ${pnlPos ? styles.pnlPos : styles.pnlNeg}`}>
            {pnlPos ? '+' : ''}${s.total_net_pnl.toFixed(4)}
          </div>
        </div>
        <div className={styles.statCell}>
          <div className={styles.statLabel}>Breakeven</div>
          <div className={styles.statValue}>{s.breakeven_pct.toFixed(3)}%</div>
        </div>
        {winRate !== null && (
          <div className={styles.statCell}>
            <div className={styles.statLabel}>Open rate</div>
            <div className={styles.statValue}>{winRate.toFixed(0)}%</div>
          </div>
        )}
      </div>
      {stats.pairs.length > 0 && (
        <div className={styles.openPositionsLabel}>
          Open positions: {stats.pairs.map((p) =>
            `${p.symbol} ${p.exchange_long}→${p.exchange_short} @${p.entry_spread.toFixed(3)}%`
          ).join(' · ')}
        </div>
      )}
      {stats.updated_at && (
        <div className={styles.statsUpdated}>
          Stats updated: {new Date(stats.updated_at).toLocaleTimeString()}
        </div>
      )}
      <a
        href="https://mexc-trade-bot-production.up.railway.app/api/arbitrage/research/export-dataset"
        download
        className={styles.csvBtn}
      >
        ⬇ Raw CSV
      </a>
      <a
        href="https://mexc-trade-bot-production.up.railway.app/api/arbitrage/research/export-dataset?clean=true"
        download
        className={`${styles.csvBtn} ${styles.csvBtnClean}`}
      >
        🧹 Clean CSV
      </a>
    </div>
  );
}

/* ─────────────────── Tab 1: Research ─────────────────── */

const REFRESH_INTERVAL = 30_000;
const REFRESH_SECONDS = REFRESH_INTERVAL / 1000;

function ResearchTab() {
  const [pairs, setPairs] = useState<ArbPair[]>([]);
  const [stats, setStats] = useState<ResearchStats | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [countdown, setCountdown] = useState(REFRESH_SECONDS);
  const [entryMode, setEntryMode] = useState<EntryModeFilter>('all');
  const entryModeRef = useRef<EntryModeFilter>('all');

  const fetchPairs = useCallback(async () => {
    const mode = entryModeRef.current;
    const qs = mode !== 'all' ? `?entry_mode=${mode}` : '';
    const r = await fetch(`/api/proxy/api/arbitrage/research/pairs${qs}`);
    if (!r.ok) return;
    const data = await r.json() as { pairs: ArbPair[] };
    setPairs(data.pairs ?? []);
    setUpdatedAt(new Date().toLocaleTimeString());
    setCountdown(REFRESH_SECONDS);
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const r = await fetch('/api/proxy/api/arbitrage/research/stats');
      if (!r.ok) return;
      const data = await r.json() as ResearchStats;
      if (data.session) setStats(data);
    } catch { /* ignore */ }
  }, []);

  usePolling(fetchPairs, REFRESH_INTERVAL);
  usePolling(fetchStats, 60_000);

  useEffect(() => {
    const id = setInterval(() => {
      setCountdown((prev) => (prev <= 1 ? REFRESH_SECONDS : prev - 1));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  // Re-fetch when entry mode changes
  useEffect(() => {
    entryModeRef.current = entryMode;
    fetchPairs();
  }, [entryMode, fetchPairs]);

  const MODE_LABELS: Record<EntryModeFilter, string> = {
    all: 'All',
    zscore: 'ZScore Only',
    large_spread: 'Large Spread Only',
  };

  return (
    <div>
      <div className={styles.sseIndicator}>
        <span className={updatedAt ? styles.sseDot : styles.sseDotOff} />
        {updatedAt ? `Updated ${updatedAt} · next in ${countdown}s` : 'Loading…'}
      </div>

      {/* Entry mode toggle */}
      <div className={styles.entryModeBar}>
        {(['all', 'zscore', 'large_spread'] as EntryModeFilter[]).map((m) => (
          <button
            key={m}
            className={`${styles.modeBtn} ${entryMode === m ? styles.modeBtnActive : ''}`}
            onClick={() => setEntryMode(m)}
          >
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>

      <div className={styles.tableCard}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>Symbol</th>
              <th className={styles.th}>Long Exchange</th>
              <th className={styles.th}>Short Exchange</th>
              <th className={styles.th}>Price ↓ buy</th>
              <th className={styles.th}>Price ↑ sell</th>
              <th className={styles.th}>Spread %</th>
              <th className={styles.th}>Z-Score</th>
              <th className={styles.th}>Mode</th>
              <th className={styles.th}>Status</th>
            </tr>
          </thead>
          <tbody>
            {pairs.map((row, i) => (
              <tr key={i} className={styles.row}>
                <td className={styles.td}>
                  <span className={styles.symbol}>{row.symbol}</span>
                </td>
                <td className={styles.td}>
                  <span className={styles.exchange}>{row.exchange_long}</span>
                </td>
                <td className={styles.td}>
                  <span className={styles.exchange}>{row.exchange_short}</span>
                </td>
                <td className={styles.td}>
                  <span className={styles.priceLow}>{fmtPrice(row.price_long)}</span>
                </td>
                <td className={styles.td}>
                  <span className={styles.priceHigh}>{fmtPrice(row.price_short)}</span>
                </td>
                <td className={styles.td}>
                  <span className={styles.spreadValue}>{fmtPct(row.spread_pct)}</span>
                </td>
                <td className={styles.td}>
                  <span className={styles.muted}>
                    {row.zscore != null ? row.zscore.toFixed(2) : '—'}
                  </span>
                </td>
                <td className={styles.td}>
                  <EntryModeBadge mode={row.entry_mode} />
                </td>
                <td className={styles.td}>
                  <StatusBadge status={row.status} />
                </td>
              </tr>
            ))}
            {pairs.length === 0 && (
              <tr>
                <td colSpan={9} className={styles.emptyCell}>
                  Waiting for data…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <StatsPanel stats={stats} />
    </div>
  );
}

/* ─────────────────── Tab 2: Queue ─────────────────── */

function QueueTab({ onCountChange }: { onCountChange: (n: number) => void }) {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastIdRef = useRef(0);

  const addToast = useCallback((message: string, type: ToastItem['type'] = 'success') => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3000);
  }, []);

  const removeItem = useCallback(
    (id: number) => {
      setItems((prev) => {
        const next = prev.filter((i) => i.id !== id);
        onCountChange(next.length);
        return next;
      });
    },
    [onCountChange],
  );

  const fetchQueue = useCallback(async () => {
    const r = await fetch('/api/proxy/api/arbitrage/queue');
    if (!r.ok) return;
    const data = await r.json() as { items: QueueItem[] };
    const list = data.items ?? [];
    setItems(list);
    onCountChange(list.length);
  }, [onCountChange]);

  usePolling(fetchQueue, 30_000);

  const approve = async (id: number) => {
    try {
      await fetch(`/api/proxy/api/arbitrage/queue/${id}/approve`, { method: 'POST' });
      removeItem(id);
      addToast('Pair approved and added to watchlist ✅');
    } catch {
      addToast('Approve failed', 'error');
    }
  };

  const reject = async (id: number) => {
    if (!confirm('Reject this pair? It will be removed from the queue.')) return;
    try {
      await fetch(`/api/proxy/api/arbitrage/queue/${id}/reject`, { method: 'POST' });
      removeItem(id);
      addToast('Pair rejected', 'error');
    } catch {
      addToast('Reject failed', 'error');
    }
  };

  const snooze = async (id: number) => {
    try {
      await fetch(`/api/proxy/api/arbitrage/queue/${id}/snooze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hours: 24 }),
      });
      removeItem(id);
      addToast('Snoozed for 24h ⏰');
    } catch {
      addToast('Snooze failed', 'error');
    }
  };

  return (
    <>
      <ToastStack items={toasts} />
      {items.length === 0 ? (
        <div className={styles.emptyState}>No pairs in queue.</div>
      ) : (
        <div className={styles.cards}>
          {items.map((item) => (
            <div key={item.id} className={styles.card}>
              <div className={styles.cardHeader}>
                <span className={styles.symbol}>{item.symbol}</span>
                <span className={styles.muted}>
                  {item.exchange_long} ←→ {item.exchange_short}
                </span>
                <span className={styles.score}>{item.score.toFixed(0)}pts</span>
              </div>
              <div className={styles.cardMeta}>
                Win: {(item.win_rate * 100).toFixed(0)}% | Signals:{' '}
                {item.signals_per_day.toFixed(1)}/day | Hold:{' '}
                {item.avg_hold_minutes.toFixed(0)}min
              </div>
              <div className={styles.cardMeta}>
                Avg PnL:{' '}
                {item.avg_net_pnl_usdt >= 0 ? '+' : ''}${item.avg_net_pnl_usdt.toFixed(2)} per
                $10 | {item.total_paper_trades} trades | {item.days_observed}d observed
              </div>
              <div className={styles.cardActions}>
                <button className={styles.btnApprove} onClick={() => approve(item.id)}>
                  ✅ Approve
                </button>
                <button className={styles.btnSnooze} onClick={() => snooze(item.id)}>
                  ⏰ Snooze 24h
                </button>
                <button className={styles.btnReject} onClick={() => reject(item.id)}>
                  ❌ Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ─────────────────── Tab 3: Active ─────────────────── */

function ActiveTab({ onCountChange }: { onCountChange: (n: number) => void }) {
  const [positions, setPositions] = useState<ActivePosition[]>([]);
  const [totalPnl, setTotalPnl] = useState(0);

  const fetchActive = useCallback(async () => {
    const r = await fetch('/api/proxy/api/arbitrage/active');
    if (!r.ok) return;
    const data = await r.json() as { positions: ActivePosition[]; total_paper_pnl: number };
    const list = data.positions ?? [];
    setPositions(list);
    setTotalPnl(data.total_paper_pnl ?? 0);
    onCountChange(list.length);
  }, [onCountChange]);

  usePolling(fetchActive, 30_000);

  return (
    <div>
      {positions.length === 0 ? (
        <div className={styles.emptyState}>
          No active positions. Approve pairs in Queue tab to start.
        </div>
      ) : (
        <div className={styles.tableCard}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>Symbol</th>
                <th className={styles.th}>Exchanges</th>
                <th className={styles.th}>Entry Spread</th>
                <th className={styles.th}>Current Spread</th>
                <th className={styles.th}>Size</th>
                <th className={styles.th}>Hold Time</th>
                <th className={styles.th}>PnL</th>
                <th className={styles.th}>Mode</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => (
                <tr key={pos.id} className={styles.row}>
                  <td className={styles.td}>
                    <span className={styles.symbol}>{pos.symbol}</span>
                  </td>
                  <td className={styles.td}>
                    <span className={styles.exchange}>
                      {pos.exchange_long} / {pos.exchange_short}
                    </span>
                  </td>
                  <td className={styles.td}>{fmtPct(pos.entry_spread_pct)}</td>
                  <td className={styles.td}>{fmtPct(pos.current_spread_pct)}</td>
                  <td className={styles.td}>${pos.size_usdt.toFixed(0)}</td>
                  <td className={styles.td}>{fmtHold(pos.hold_minutes)}</td>
                  <td className={styles.td}>
                    <span className={pos.unrealized_pnl_usdt >= 0 ? styles.pnlPos : styles.pnlNeg}>
                      {pos.unrealized_pnl_usdt >= 0 ? '+' : ''}$
                      {pos.unrealized_pnl_usdt.toFixed(2)}
                    </span>
                  </td>
                  <td className={styles.td}>
                    <span className={`${styles.badge} ${styles.badgePaper}`}>PAPER</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className={styles.totalPnl}>
        Total Paper PnL:{' '}
        <span className={totalPnl >= 0 ? styles.pnlPos : styles.pnlNeg}>
          {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
        </span>
      </div>
    </div>
  );
}

/* ─────────────────── Page ─────────────────── */

export default function ArbitragePage() {
  const [tab, setTab] = useState<Tab>('research');
  const [queueCount, setQueueCount] = useState(0);
  const [activeCount, setActiveCount] = useState(0);

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>Arbitrage</h1>
        <p className={styles.subtitle}>Cross-exchange spread monitoring and approval queue</p>
      </div>

      {/* Tab bar */}
      <div className={styles.tabBar}>
        <button
          className={`${styles.tab} ${tab === 'research' ? styles.tabActive : ''}`}
          onClick={() => setTab('research')}
        >
          🔬 Research
        </button>
        <button
          className={`${styles.tab} ${tab === 'queue' ? styles.tabActive : ''}`}
          onClick={() => setTab('queue')}
        >
          ⏳ Queue
          {queueCount > 0 && <span className={styles.tabCount}>{queueCount}</span>}
        </button>
        <button
          className={`${styles.tab} ${tab === 'active' ? styles.tabActive : ''}`}
          onClick={() => setTab('active')}
        >
          🟢 Active
          {activeCount > 0 && <span className={styles.tabCount}>{activeCount}</span>}
        </button>
      </div>

      {/* Tab content */}
      <div className={styles.content}>
        {tab === 'research' && <ResearchTab />}
        {tab === 'queue' && <QueueTab onCountChange={setQueueCount} />}
        {tab === 'active' && <ActiveTab onCountChange={setActiveCount} />}
      </div>
    </div>
  );
}
