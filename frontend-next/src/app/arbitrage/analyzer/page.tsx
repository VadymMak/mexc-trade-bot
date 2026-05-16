'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import Papa from 'papaparse';
import {
  Chart as ChartJS,
  ArcElement, BarElement, LineElement, PointElement,
  CategoryScale, LinearScale,
  Tooltip, Legend,
  type ChartData, type ChartOptions,
} from 'chart.js';
import { Bar, Doughnut } from 'react-chartjs-2';
import styles from './page.module.css';

ChartJS.register(ArcElement, BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend);

/* ─── Types ─── */
interface Row {
  symbol: string;
  exchange_long: string;
  exchange_short: string;
  entry_mode: string;
  entry_spread_pct: string;
  exit_reason: string;
  exit_spread_pct: string;
  net_pnl_usdt: string;
  gross_pnl_usdt: string;
  hold_seconds: string;
  trading_session: string;
  day_of_week: string;
  hour_utc: string;
  mins_to_funding: string;
  buy_pressure: string;
  trade_velocity: string;
  book_imbalance: string;
  profitable: string;
  [key: string]: string;
}

type AnalyzeData = {
  generated_at: string;
  hours: number;
  overview: {
    total_trades: number;
    wins: number;
    win_rate: number;
    net_pnl: number;
    gross_pnl: number;
    total_fees: number;
    avg_hold_seconds: number;
    avg_size: number;
    avg_pnl_per_trade: number;
  };
  tiers: { tier: number; trades: number; wins: number; win_rate: number; net_pnl: number; avg_pnl: number }[];
  exit_reasons: { reason: string; count: number }[];
  daily_pnl: { day: string; trades: number; net_pnl: number; wins: number }[];
  symbols: { symbol: string; trades: number; wins: number; win_rate: number; net_pnl: number; avg_hold: number; avg_size: number }[];
  hourly: { hour: number; trades: number; wins: number; win_rate: number; net_pnl: number }[];
  open: { count: number; exposure: number };
};

/* ─── Helpers ─── */
const COLORS = {
  green: '#4ade80', red: '#f87171', yellow: '#fbbf24',
  blue: '#60a5fa', purple: '#a78bfa', orange: '#fb923c',
  teal: '#2dd4bf', gray: '#475569',
};
const sum = (arr: number[]) => arr.reduce((a, b) => a + b, 0);
const avg = (arr: number[]) => arr.length ? sum(arr) / arr.length : 0;
const r2 = (v: number) => Math.round(v * 100) / 100;
const r4 = (v: number) => Math.round(v * 10000) / 10000;

function groupBy<T>(arr: T[], fn: (r: T) => string): Record<string, T[]> {
  return arr.reduce((acc, r) => {
    const k = fn(r) || 'unknown';
    (acc[k] ??= []).push(r);
    return acc;
  }, {} as Record<string, T[]>);
}

const barOpts = (yLabel?: string, pct?: boolean): ChartOptions<'bar'> => ({
  responsive: true, maintainAspectRatio: true,
  plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e293b' } },
    y: {
      ticks: { color: '#64748b', font: { size: 10 }, callback: pct ? (v) => v + '%' : undefined },
      grid: { color: '#334155' },
      title: yLabel ? { display: true, text: yLabel, color: '#64748b', font: { size: 10 } } : undefined,
    },
  },
});

const SESSION_ORDER = ['asia', 'europe', 'overlap', 'us', 'quiet'];
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const PHANTOM_EXCHANGES = new Set(['binance', 'bybit']);
const ZR_MIN_HOLD = 120;

function applyFilters(rows: Row[], clean: boolean): Row[] {
  if (!clean) return rows;
  return rows.filter(r => {
    if (PHANTOM_EXCHANGES.has(r.exchange_long?.toLowerCase()) ||
        PHANTOM_EXCHANGES.has(r.exchange_short?.toLowerCase())) return false;
    if (r.exit_reason === 'ZSCORE_REVERT' && parseFloat(r.hold_seconds) < ZR_MIN_HOLD) return false;
    return true;
  });
}

/* ─── Live dashboard helpers ─── */
const PERIODS = [
  { label: '6h',  hours: 6 },
  { label: '24h', hours: 24 },
  { label: '7d',  hours: 168 },
  { label: '30d', hours: 720 },
];

function wrColor(wr: number): string {
  if (wr >= 0.9) return COLORS.green;
  if (wr >= 0.7) return COLORS.yellow;
  return COLORS.red;
}
function wrBg(wr: number): string {
  if (wr >= 0.9) return '#16a34a18';
  if (wr >= 0.7) return '#b4530918';
  return '#dc262618';
}

/* ─── SVG bar chart for daily PnL ─── */
function DailyBars({ data }: { data: AnalyzeData['daily_pnl'] }) {
  const last14 = data.slice(-14);
  if (!last14.length) return <p style={{ color: '#475569', fontSize: 12 }}>No data yet.</p>;
  const maxAbs = Math.max(...last14.map(d => Math.abs(d.net_pnl)), 0.01);
  const BAR_H = 60;
  const CELL = 48;
  const svgW = last14.length * CELL;

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${svgW} 148`} style={{ width: '100%', minWidth: svgW, display: 'block' }}>
        {last14.map((d, i) => {
          const h = Math.max((Math.abs(d.net_pnl) / maxAbs) * BAR_H, 2);
          const pos = d.net_pnl >= 0;
          const x = i * CELL + 6;
          const barY = pos ? 72 - h : 72;
          const color = pos ? COLORS.green : COLORS.red;
          const label = (pos ? '+' : '') + d.net_pnl.toFixed(1);
          return (
            <g key={d.day}>
              <rect x={x} y={barY} width={36} height={h}
                fill={color + '88'} stroke={color} strokeWidth={0.5} rx={2} />
              <text x={x + 18} y={pos ? Math.max(barY - 3, 8) : barY + h + 11}
                textAnchor="middle" fontSize={9} fill={color}>{label}</text>
              <text x={x + 18} y={140} textAnchor="middle" fontSize={7.5} fill="#475569">
                {d.day.slice(5)}
              </text>
            </g>
          );
        })}
        <line x1={0} y1={72} x2={svgW} y2={72} stroke="#334155" strokeWidth={1} />
      </svg>
    </div>
  );
}

/* ─── Hourly heatmap ─── */
function HourlyHeatmap({ data }: { data: AnalyzeData['hourly'] }) {
  const byHour: Record<number, AnalyzeData['hourly'][0]> = {};
  data.forEach(h => { byHour[h.hour] = h; });

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {Array.from({ length: 24 }, (_, h) => {
        const d = byHour[h];
        const hasData = d && d.trades >= 3;
        const bg = hasData ? wrBg(d.win_rate) : '#1e293b';
        const borderColor = hasData ? wrColor(d.win_rate) : '#334155';
        const tooltip = hasData
          ? `${h}:00 UTC — ${d.trades} trades · WR ${(d.win_rate * 100).toFixed(0)}% · PnL ${d.net_pnl >= 0 ? '+' : ''}$${d.net_pnl.toFixed(2)}`
          : `${h}:00 UTC — no data`;
        return (
          <div key={h} title={tooltip} style={{
            width: 38, height: 38, borderRadius: 6,
            background: bg, border: `1px solid ${borderColor}`,
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            cursor: 'default', userSelect: 'none',
          }}>
            <span style={{ fontSize: 8, color: '#64748b', lineHeight: 1 }}>{h}h</span>
            {hasData && (
              <span style={{ fontSize: 8.5, color: wrColor(d.win_rate), fontWeight: 700, lineHeight: 1.2 }}>
                {(d.win_rate * 100).toFixed(0)}%
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ─── LiveDashboard ─── */
function LiveDashboard() {
  const [data, setData] = useState<AnalyzeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hours, setHours] = useState(24);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [showWorst, setShowWorst] = useState(false);

  const load = useCallback(async (h: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/proxy/api/arbitrage/analyze?hours=${h}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json() as AnalyzeData;
      setData(json);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(hours); }, [hours, load]);

  if (loading) {
    return (
      <div className={styles.page} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 280 }}>
        <span style={{ color: '#64748b', fontSize: 14 }}>Loading live data…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className={styles.page}>
        <div className={styles.chartCard} style={{ color: COLORS.red }}>
          Error: {error}
          <button className={styles.resetBtn} style={{ marginLeft: 12 }} onClick={() => load(hours)}>Retry</button>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { overview: ov, tiers, exit_reasons, daily_pnl, symbols, hourly, open } = data;
  const totalExits = exit_reasons.reduce((s, r) => s + r.count, 0);
  const projMonth = ov.avg_pnl_per_trade * 285 * 30;
  const topSymbols = symbols.slice(0, 10);
  const worstSymbols = [...symbols].reverse().slice(0, 5);
  const displaySymbols = showWorst ? worstSymbols : topSymbols;

  const exitColor = (reason: string) => {
    if (reason === 'TAKE_PROFIT') return COLORS.green;
    if (reason === 'STOP_LOSS')   return COLORS.red;
    if (reason === 'ZSCORE_REVERT') return COLORS.blue;
    return COLORS.yellow;
  };

  return (
    <div className={styles.page}>
      {/* Period selector + Refresh */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
        {PERIODS.map(p => (
          <button key={p.hours} onClick={() => { setHours(p.hours); }}
            style={{
              padding: '4px 12px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
              background: hours === p.hours ? '#1e3a5f' : '#1e293b',
              border: `1px solid ${hours === p.hours ? '#3b82f6' : '#334155'}`,
              color: hours === p.hours ? '#60a5fa' : '#64748b',
              fontWeight: hours === p.hours ? 700 : 400,
            }}>
            {p.label}
          </button>
        ))}
        <button onClick={() => load(hours)} className={styles.resetBtn} style={{ marginLeft: 'auto' }}>
          ↻ Refresh
        </button>
        {lastUpdated && (
          <span style={{ fontSize: 11, color: '#475569' }}>
            Updated {lastUpdated.toLocaleTimeString()}
          </span>
        )}
      </div>

      {/* Open positions badge */}
      {open.count > 0 && (
        <div style={{ marginBottom: 12 }}>
          <span style={{
            background: '#1d4ed822', border: '1px solid #3b82f655',
            color: COLORS.blue, fontSize: 12, padding: '3px 10px', borderRadius: 99,
          }}>
            {open.count} open · ${open.exposure.toFixed(0)} exposure
          </span>
        </div>
      )}

      {/* Overview cards */}
      <div className={styles.statsRow} style={{ marginBottom: 16 }}>
        {[
          { label: 'Net PnL',     value: `${ov.net_pnl >= 0 ? '+' : ''}$${ov.net_pnl.toFixed(2)}`,                     color: ov.net_pnl >= 0 ? COLORS.green : COLORS.red },
          { label: 'Win Rate',    value: `${(ov.win_rate * 100).toFixed(1)}%`,                                            color: wrColor(ov.win_rate) },
          { label: 'Trades',      value: ov.total_trades.toLocaleString(),                                                color: '#e2e8f0' },
          { label: 'Avg / trade', value: `${ov.avg_pnl_per_trade >= 0 ? '+' : ''}$${ov.avg_pnl_per_trade.toFixed(3)}`,  color: ov.avg_pnl_per_trade >= 0 ? COLORS.green : COLORS.red },
          { label: 'Proj / mo',   value: `${projMonth >= 0 ? '+' : ''}$${projMonth.toFixed(0)}`,                         color: projMonth >= 0 ? COLORS.teal : COLORS.red },
        ].map(({ label, value, color }) => (
          <div key={label} className={styles.statCard}>
            <div className={styles.statLabel}>{label}</div>
            <div className={styles.statValue} style={{ fontSize: 18, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Vel-tier table */}
      <div className={styles.tableCard} style={{ marginBottom: 16 }}>
        <div className={styles.chartTitle}>Vel-Tier Breakdown</div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr>
              <th>Tier (deal size)</th><th>Trades</th><th>Win Rate</th><th>Net PnL</th><th>Avg / trade</th>
            </tr></thead>
            <tbody>
              {tiers.map(t => (
                <tr key={t.tier} style={{ background: wrBg(t.win_rate) }}>
                  <td><strong style={{ color: COLORS.blue }}>${t.tier.toFixed(0)}</strong>
                    <span style={{ color: '#475569', fontSize: 10, marginLeft: 6 }}>
                      {t.tier <= 10 ? 'vel<10' : t.tier <= 20 ? 'vel 10-50' : 'vel>50'}
                    </span>
                  </td>
                  <td>{t.trades}</td>
                  <td style={{ color: wrColor(t.win_rate), fontWeight: 700 }}>{(t.win_rate * 100).toFixed(1)}%</td>
                  <td style={{ color: t.net_pnl >= 0 ? COLORS.green : COLORS.red, fontWeight: 700 }}>
                    {t.net_pnl >= 0 ? '+' : ''}${t.net_pnl.toFixed(2)}
                  </td>
                  <td style={{ color: t.avg_pnl >= 0 ? COLORS.green : COLORS.red }}>
                    {t.avg_pnl >= 0 ? '+' : ''}${t.avg_pnl.toFixed(4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Daily PnL + Exit reasons */}
      <div className={styles.chartsRow} style={{ marginBottom: 16 }}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>PnL by Day (last 30 days)</div>
          <DailyBars data={daily_pnl} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Exit Reasons</div>
          <div style={{ marginTop: 8 }}>
            {exit_reasons.map(r => {
              const pct = totalExits > 0 ? (r.count / totalExits * 100).toFixed(1) : '0.0';
              const color = exitColor(r.reason);
              return (
                <div key={r.reason} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <span style={{ color, fontSize: 12, fontWeight: 600 }}>{r.reason}</span>
                  <span style={{ color, fontSize: 13, fontWeight: 700 }}>
                    {pct}%
                    <span style={{ color: '#475569', fontWeight: 400, fontSize: 11, marginLeft: 5 }}>({r.count})</span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Symbols table */}
      <div className={styles.tableCard} style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
          <span className={styles.chartTitle} style={{ margin: 0 }}>
            {showWorst ? 'Worst 5 Symbols' : 'Top 10 Symbols'}
          </span>
          <button onClick={() => setShowWorst(v => !v)} className={styles.resetBtn} style={{ marginLeft: 'auto' }}>
            {showWorst ? 'Show top 10' : 'Show worst 5'}
          </button>
        </div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr>
              <th>Symbol</th><th>Trades</th><th>Win Rate</th><th>Net PnL</th><th>Avg Hold</th><th>Avg Size</th>
            </tr></thead>
            <tbody>
              {displaySymbols.map(s => (
                <tr key={s.symbol}>
                  <td>
                    <strong>{s.symbol.replace('_USDT', '')}</strong>
                    <span style={{ color: '#475569', fontSize: 10 }}>_USDT</span>
                  </td>
                  <td>{s.trades}</td>
                  <td style={{ color: wrColor(s.win_rate), fontWeight: 700 }}>{(s.win_rate * 100).toFixed(1)}%</td>
                  <td style={{ color: s.net_pnl >= 0 ? COLORS.green : COLORS.red, fontWeight: 700 }}>
                    {s.net_pnl >= 0 ? '+' : ''}${s.net_pnl.toFixed(2)}
                  </td>
                  <td>{s.avg_hold < 60 ? `${s.avg_hold.toFixed(0)}s` : `${(s.avg_hold / 60).toFixed(1)}m`}</td>
                  <td>${s.avg_size.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Hourly heatmap */}
      <div className={styles.chartCard}>
        <div className={styles.chartTitle}>Hourly Win Rate (UTC) — hover for details</div>
        <HourlyHeatmap data={hourly} />
      </div>
    </div>
  );
}

/* ─── CsvAnalyzer (formerly AnalyzerPage) ─── */
function CsvAnalyzer() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [clean, setClean] = useState(true);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const parse = useCallback((file: File) => {
    Papa.parse<Row>(file, {
      header: true, skipEmptyLines: true,
      complete: (r) => setRows(r.data),
    });
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f?.name.endsWith('.csv')) parse(f);
  }, [parse]);

  if (!rows) {
    return (
      <div className={styles.page}>
        <div
          className={`${styles.dropZone} ${dragging ? styles.dragging : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => fileRef.current?.click()}
        >
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#475569" strokeWidth="1.5">
            <path d="M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>
          </svg>
          <p><strong>Drop your dataset CSV here</strong> or click to browse</p>
          <p className={styles.hint}>Download via the ⬇ CSV button on the Arbitrage page</p>
          <input ref={fileRef} type="file" accept=".csv" style={{ display: 'none' }}
            onChange={(e) => e.target.files?.[0] && parse(e.target.files[0])} />
        </div>
      </div>
    );
  }

  const filtered = applyFilters(rows, clean);
  const removed  = rows.length - filtered.length;

  return (
    <Dashboard
      rows={filtered}
      totalRaw={rows.length}
      removed={removed}
      clean={clean}
      onCleanToggle={() => setClean(v => !v)}
      onReset={() => setRows(null)}
    />
  );
}

/* ─── AnalyzerPage — tab switcher (default export) ─── */
export default function AnalyzerPage() {
  const [tab, setTab] = useState<'live' | 'csv'>('live');
  return (
    <>
      <div style={{
        display: 'flex', gap: 6, padding: '10px 24px',
        borderBottom: '1px solid #334155', background: '#0f172a',
        position: 'sticky', top: 0, zIndex: 10,
      }}>
        {(['live', 'csv'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '5px 16px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
            background: tab === t ? '#1e3a5f' : '#1e293b',
            border: `1px solid ${tab === t ? '#3b82f6' : '#334155'}`,
            color: tab === t ? '#60a5fa' : '#64748b',
            fontWeight: tab === t ? 700 : 400,
          }}>
            {t === 'live' ? '⚡ Live' : '📁 CSV History'}
          </button>
        ))}
      </div>
      {tab === 'live' ? <LiveDashboard /> : <CsvAnalyzer />}
    </>
  );
}

/* ─── Dashboard (CSV results — unchanged) ─── */
function Dashboard({ rows, totalRaw, removed, clean, onCleanToggle, onReset }: {
  rows: Row[];
  totalRaw: number;
  removed: number;
  clean: boolean;
  onCleanToggle: () => void;
  onReset: () => void;
}) {
  const total   = rows.length;
  const netPnl  = sum(rows.map(r => parseFloat(r.net_pnl_usdt) || 0));
  const grossPnl = sum(rows.map(r => parseFloat(r.gross_pnl_usdt) || 0));
  const tpRows  = rows.filter(r => r.exit_reason === 'TAKE_PROFIT');
  const slRows  = rows.filter(r => r.exit_reason === 'STOP_LOSS');
  const zrRows  = rows.filter(r => r.exit_reason === 'ZSCORE_REVERT');
  const expRows = rows.filter(r => r.exit_reason === 'EXPIRED');
  const tsRows  = rows.filter(r => r.exit_reason === 'TIME_STOP');
  const avgHold = avg(rows.map(r => parseFloat(r.hold_seconds) || 0)) / 60;
  const avgEntry = avg(rows.map(r => parseFloat(r.entry_spread_pct) || 0));

  /* Exit pie */
  const exitData: ChartData<'doughnut'> = {
    labels: ['TAKE_PROFIT', 'ZSCORE_REVERT', 'STOP_LOSS', 'TIME_STOP', 'EXPIRED', 'OTHER'],
    datasets: [{ data: [tpRows.length, zrRows.length, slRows.length, tsRows.length, expRows.length,
      rows.length - tpRows.length - zrRows.length - slRows.length - tsRows.length - expRows.length],
      backgroundColor: [`${COLORS.green}cc`, `${COLORS.blue}cc`, `${COLORS.red}cc`,
        `${COLORS.yellow}cc`, `${COLORS.orange}cc`, `${COLORS.gray}cc`],
      borderWidth: 0 }],
  };

  /* PnL by session */
  const sessionGroups = groupBy(rows, r => r.trading_session);
  const sessionLabels = SESSION_ORDER.filter(s => sessionGroups[s]);
  const sessionPnl = sessionLabels.map(s => r2(sum(sessionGroups[s].map(r => parseFloat(r.net_pnl_usdt) || 0))));
  const sessionData: ChartData<'bar'> = {
    labels: sessionLabels,
    datasets: [{ label: 'Net PnL (USDT)', data: sessionPnl,
      backgroundColor: sessionPnl.map(v => v >= 0 ? `${COLORS.green}99` : `${COLORS.red}99`),
      borderColor: sessionPnl.map(v => v >= 0 ? COLORS.green : COLORS.red),
      borderWidth: 1, borderRadius: 4 }],
  };

  /* WR by entry spread bucket */
  const bins   = [0, 0.3, 0.5, 0.6, 0.7, 0.8, 1.0, 1.5, 2.0, 5.0, 999];
  const bLabels = ['<0.3', '0.3-0.5', '0.5-0.6', '0.6-0.7', '0.7-0.8', '0.8-1.0', '1.0-1.5', '1.5-2.0', '2.0-5.0', '>5.0'];
  const bWR = bLabels.map((_, i) => {
    const g = rows.filter(r => { const v = parseFloat(r.entry_spread_pct) || 0; return v >= bins[i] && v < bins[i+1]; });
    return g.length ? r2(g.filter(r => r.exit_reason === 'TAKE_PROFIT').length / g.length * 100) : null;
  });
  const bCnt = bLabels.map((_, i) =>
    rows.filter(r => { const v = parseFloat(r.entry_spread_pct) || 0; return v >= bins[i] && v < bins[i+1]; }).length
  );
  const bPnl = bLabels.map((_, i) =>
    r2(sum(rows.filter(r => { const v = parseFloat(r.entry_spread_pct)||0; return v >= bins[i] && v < bins[i+1]; })
      .map(r => parseFloat(r.net_pnl_usdt)||0)))
  );
  const spreadWRData: ChartData<'bar'> = {
    labels: bLabels,
    datasets: [
      { label: 'Win Rate %', data: bWR as number[],
        backgroundColor: (bWR as (number|null)[]).map(v => v === null ? '#33415588' : v >= 50 ? `${COLORS.green}88` : `${COLORS.red}88`),
        borderColor: (bWR as (number|null)[]).map(v => v === null ? '#334155' : v >= 50 ? COLORS.green : COLORS.red),
        borderWidth: 1, borderRadius: 3, yAxisID: 'y' },
      { label: 'Trades', data: bCnt, type: 'bar' as const,
        backgroundColor: `${COLORS.blue}44`, borderColor: `${COLORS.blue}88`, borderWidth: 1, yAxisID: 'y1' },
    ],
  };

  /* PnL by spread bucket */
  const spreadPnlData: ChartData<'bar'> = {
    labels: bLabels,
    datasets: [{ label: 'Net PnL', data: bPnl,
      backgroundColor: bPnl.map(v => v >= 0 ? `${COLORS.green}88` : `${COLORS.red}88`),
      borderColor: bPnl.map(v => v >= 0 ? COLORS.green : COLORS.red),
      borderWidth: 1, borderRadius: 3 }],
  };

  /* Hourly WR */
  const hourGroups = groupBy(rows, r => r.hour_utc);
  const hourWR = Array.from({length: 24}, (_, h) => {
    const g = hourGroups[String(h)] || [];
    return g.length >= 5 ? r2(g.filter(r => r.exit_reason === 'TAKE_PROFIT').length / g.length * 100) : null;
  });
  const hourCnt = Array.from({length: 24}, (_, h) => (hourGroups[String(h)] || []).length);
  const hourData: ChartData<'bar'> = {
    labels: Array.from({length: 24}, (_, h) => `${h}h`),
    datasets: [
      { label: 'Win Rate %', data: hourWR as number[],
        backgroundColor: (hourWR as (number|null)[]).map(v => v === null ? '#33415544' : v >= 50 ? `${COLORS.green}88` : `${COLORS.red}88`),
        borderColor: (hourWR as (number|null)[]).map(v => v === null ? '#334155' : v >= 50 ? COLORS.green : COLORS.red),
        borderWidth: 1, yAxisID: 'y' },
      { label: 'Trades', data: hourCnt, type: 'bar' as const,
        backgroundColor: `${COLORS.blue}44`, yAxisID: 'y1' },
    ],
  };

  /* Funding proximity */
  const fBins   = [0, 5, 15, 30, 60, 120, 240, 9999];
  const fLabels = ['0-5m', '5-15m', '15-30m', '30-60m', '1-2h', '2-4h', '>4h'];
  const fBuckets = fLabels.map((_, i) => rows.filter(r => {
    const v = parseFloat(r.mins_to_funding); return !isNaN(v) && v >= fBins[i] && v < fBins[i+1];
  }));
  const fWR  = fBuckets.map(g => g.length >= 3 ? r2(g.filter(r => r.exit_reason === 'TAKE_PROFIT').length / g.length * 100) : null);
  const fPnl = fBuckets.map(g => r2(sum(g.map(r => parseFloat(r.net_pnl_usdt)||0))));
  const fundingData: ChartData<'bar'> = {
    labels: fLabels,
    datasets: [
      { label: 'Win Rate %', data: fWR as number[],
        backgroundColor: (fWR as (number|null)[]).map(v => v===null?'#33415544':v>=50?`${COLORS.green}88`:`${COLORS.red}88`),
        borderColor: (fWR as (number|null)[]).map(v => v===null?'#334155':v>=50?COLORS.green:COLORS.red),
        borderWidth: 1, borderRadius: 4, yAxisID: 'y' },
      { label: 'Net PnL', data: fPnl, type: 'bar' as const,
        backgroundColor: fPnl.map(v => v>=0?`${COLORS.teal}66`:`${COLORS.orange}66`),
        borderColor: fPnl.map(v => v>=0?COLORS.teal:COLORS.orange),
        borderWidth: 1, yAxisID: 'y1' },
    ],
  };

  /* Exchange pair table */
  const exGroups = groupBy(rows, r => `${r.exchange_long}|${r.exchange_short}`);
  const exData = Object.entries(exGroups).map(([combo, g]) => {
    const [exL, exS] = combo.split('|');
    const tp = g.filter(r => r.exit_reason === 'TAKE_PROFIT').length;
    return {
      exL, exS, count: g.length,
      wr: tp / g.length * 100,
      avgEntry: avg(g.map(r => parseFloat(r.entry_spread_pct)||0)),
      avgHold: avg(g.map(r => parseFloat(r.hold_seconds)||0)) / 60,
      netPnl: sum(g.map(r => parseFloat(r.net_pnl_usdt)||0)),
    };
  }).sort((a, b) => b.count - a.count);

  /* Symbol table */
  const symGroups = groupBy(rows, r => r.symbol);
  const symData = Object.entries(symGroups).map(([sym, g]) => ({
    sym, count: g.length,
    tp: g.filter(r => r.exit_reason === 'TAKE_PROFIT').length,
    sl: g.filter(r => r.exit_reason === 'STOP_LOSS').length,
    wr: g.filter(r => r.exit_reason === 'TAKE_PROFIT').length / g.length * 100,
    avgEntry: avg(g.map(r => parseFloat(r.entry_spread_pct)||0)),
    netPnl: sum(g.map(r => parseFloat(r.net_pnl_usdt)||0)),
  })).sort((a, b) => a.netPnl - b.netPnl);

  /* Flow features */
  const flowRows = rows.filter(r => r.buy_pressure !== '' && !isNaN(parseFloat(r.buy_pressure)));

  return (
    <div className={styles.page}>
      {/* Header */}
      <div className={styles.header}>
        <span className={styles.title}>📊 Dataset Analyzer</span>
        <span className={styles.badge}>{total.toLocaleString()} trades</span>
        {clean && removed > 0 && (
          <span className={styles.badgeDirty}>🧹 {removed} dirty removed</span>
        )}
        <label className={styles.cleanToggle}>
          <input type="checkbox" checked={clean} onChange={onCleanToggle} />
          <span>Clean data</span>
          <span className={styles.cleanHint}>
            {clean
              ? '✓ removing binance/bybit phantom pairs + ZR exits &lt;120s'
              : '⚠ showing raw data including dirty trades'}
          </span>
        </label>
        <span className={styles.rawHint}>Raw: {totalRaw.toLocaleString()}</span>
        <button className={styles.resetBtn} onClick={onReset}>Load new CSV</button>
      </div>

      {/* Stats row */}
      <div className={styles.statsRow}>
        {[
          { label: 'Net PnL', value: `${netPnl >= 0 ? '+' : ''}$${r2(netPnl)}`, cls: netPnl >= 0 ? styles.green : styles.red },
          { label: 'Gross PnL', value: `${grossPnl >= 0 ? '+' : ''}$${r2(grossPnl)}`, cls: grossPnl >= 0 ? styles.green : styles.red },
          { label: 'Fees + Slip', value: `$${r2(netPnl - grossPnl)}`, cls: styles.red },
          { label: 'TP Rate', value: `${(tpRows.length/total*100).toFixed(1)}%`, cls: tpRows.length/total > 0.4 ? styles.green : styles.yellow },
          { label: 'ZR Rate', value: `${(zrRows.length/total*100).toFixed(1)}%`, cls: zrRows.length/total > 0.5 ? styles.red : styles.yellow },
          { label: 'SL Rate', value: `${(slRows.length/total*100).toFixed(1)}%`, cls: styles.red },
          { label: 'Avg Entry', value: `${avgEntry.toFixed(3)}%`, cls: styles.blue },
          { label: 'Avg Hold', value: `${avgHold.toFixed(1)} min`, cls: styles.blue },
        ].map(({ label, value, cls }) => (
          <div key={label} className={styles.statCard}>
            <div className={styles.statLabel}>{label}</div>
            <div className={`${styles.statValue} ${cls}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Charts row 1 */}
      <div className={styles.chartsRow}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Exit Reason Split</div>
          <Doughnut data={exitData} options={{ responsive: true, maintainAspectRatio: true,
            plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } } }} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>PnL by Session</div>
          <Bar data={sessionData} options={barOpts('Net PnL USDT')} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Hourly Win Rate (UTC)</div>
          <Bar data={hourData} options={{
            ...barOpts(),
            scales: {
              x: { ticks: { color: '#64748b', font: { size: 9 } }, grid: { color: '#1e293b' } },
              y: { ticks: { color: '#64748b', callback: (v) => v + '%' }, grid: { color: '#334155' }, max: 100 },
              y1: { ticks: { color: '#475569' }, grid: { display: false }, position: 'right' as const },
            },
          }} />
        </div>
      </div>

      {/* Charts row 2 — spread analysis */}
      <div className={styles.chartsRow}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Win Rate by Entry Spread Bucket</div>
          <Bar data={spreadWRData} options={{
            ...barOpts(),
            scales: {
              x: { ticks: { color: '#64748b', font: { size: 9 } }, grid: { color: '#1e293b' } },
              y: { ticks: { color: '#64748b', callback: (v) => v + '%' }, max: 100, grid: { color: '#334155' } },
              y1: { ticks: { color: '#475569' }, grid: { display: false }, position: 'right' as const },
            },
          }} />
          <p className={styles.insight}>
            <strong>Key:</strong> which spread buckets actually win? Higher bars = better entry zone.
          </p>
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Net PnL by Entry Spread Bucket</div>
          <Bar data={spreadPnlData} options={barOpts('Net PnL USDT')} />
        </div>
      </div>

      {/* Funding */}
      <div className={styles.chartCard} style={{ marginBottom: 16 }}>
        <div className={styles.chartTitle}>Funding Proximity — Win Rate & PnL (minutes before 00/08/16h UTC)</div>
        <div style={{ maxWidth: 640 }}>
          <Bar data={fundingData} options={{
            ...barOpts(),
            scales: {
              x: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } },
              y: { ticks: { color: '#64748b', callback: (v) => v + '%' }, max: 100, grid: { color: '#334155' } },
              y1: { ticks: { color: '#2dd4bf' }, grid: { display: false }, position: 'right' as const },
            },
          }} />
        </div>
      </div>

      {/* Exchange table */}
      <div className={styles.tableCard}>
        <div className={styles.chartTitle}>Exchange Pair Breakdown</div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr>
              <th>Long</th><th>Short</th><th>Trades</th>
              <th>TP Rate</th><th>Avg Entry%</th><th>Avg Hold</th><th>Net PnL</th>
            </tr></thead>
            <tbody>
              {exData.map(d => (
                <tr key={`${d.exL}-${d.exS}`}>
                  <td style={{ color: COLORS.blue }}><strong>{d.exL}</strong></td>
                  <td style={{ color: COLORS.purple }}><strong>{d.exS}</strong></td>
                  <td>{d.count}</td>
                  <td style={{ color: d.wr >= 40 ? COLORS.green : COLORS.red }}>{d.wr.toFixed(1)}%</td>
                  <td>{d.avgEntry.toFixed(3)}%</td>
                  <td>{d.avgHold.toFixed(1)}m</td>
                  <td style={{ color: d.netPnl >= 0 ? COLORS.green : COLORS.red, fontWeight: 700 }}>
                    {d.netPnl >= 0 ? '+' : ''}${r2(d.netPnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Symbol table */}
      <div className={styles.tableCard}>
        <div className={styles.chartTitle}>Symbol Breakdown (sorted by Net PnL ↑ worst first)</div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr>
              <th>Symbol</th><th>Trades</th><th>TP</th><th>SL</th>
              <th>TP Rate</th><th>Avg Entry%</th><th>Net PnL</th>
            </tr></thead>
            <tbody>
              {symData.map(d => (
                <tr key={d.sym}>
                  <td><strong>{d.sym}</strong></td>
                  <td>{d.count}</td>
                  <td><span className={styles.tagTp}>{d.tp}</span></td>
                  <td><span className={styles.tagSl}>{d.sl}</span></td>
                  <td style={{ color: d.wr >= 40 ? COLORS.green : COLORS.red }}>{d.wr.toFixed(1)}%</td>
                  <td>{d.avgEntry.toFixed(3)}%</td>
                  <td style={{ color: d.netPnl >= 0 ? COLORS.green : COLORS.red, fontWeight: 700 }}>
                    {d.netPnl >= 0 ? '+' : ''}${r4(d.netPnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Flow features */}
      <div className={styles.tableCard}>
        <div className={styles.chartTitle}>📡 Flow Features (tape + book)</div>
        {flowRows.length === 0 ? (
          <p className={styles.insight} style={{ color: COLORS.yellow }}>
            ⚠ No flow data yet — buy_pressure / trade_velocity / book_imbalance columns are empty.
            Flow collectors deployed recently — data appears in next CSV export.
          </p>
        ) : (
          <p className={styles.insight}>
            <strong style={{ color: COLORS.green }}>✓ {flowRows.length} trades with flow data</strong>
            {' '}({total - flowRows.length} older trades without).
            Avg buy_pressure: {avg(flowRows.map(r => parseFloat(r.buy_pressure)||0)).toFixed(3)}
            {' '}| Avg velocity: {avg(flowRows.map(r => parseFloat(r.trade_velocity)||0)).toFixed(1)} trades/min
          </p>
        )}
      </div>
    </div>
  );
}
