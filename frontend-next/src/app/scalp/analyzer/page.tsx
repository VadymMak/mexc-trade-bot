'use client';

import { useState, useCallback, useRef } from 'react';
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
  symbol:          string;
  exchange:        string;
  direction:       string;
  hour_utc:        string;
  day_of_week:     string;
  trading_session: string;
  is_weekend:      string;
  mm_repeat_score: string;
  buy_pressure:    string;
  trade_velocity:  string;
  book_imbalance:  string;
  spread_cv:       string;
  entry_price:     string;
  exit_price:      string;
  hold_seconds:    string;
  exit_reason:     string;
  deal_size_usdt:  string;
  gross_pnl_usdt:  string;
  net_pnl_usdt:    string;
  pnl_pct:         string;
  profitable:      string;
  [key: string]: string;
}

/* ─── Helpers ─── */
const COLORS = {
  green:  '#4ade80', red:    '#f87171', yellow: '#fbbf24',
  blue:   '#60a5fa', purple: '#a78bfa', orange: '#fb923c',
  teal:   '#2dd4bf', gray:   '#475569',
};
const sum = (arr: number[]) => arr.reduce((a, b) => a + b, 0);
const avg = (arr: number[]) => arr.length ? sum(arr) / arr.length : 0;
const r2  = (v: number) => Math.round(v * 100) / 100;
const r4  = (v: number) => Math.round(v * 10000) / 10000;

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
      grid:  { color: '#334155' },
      title: yLabel ? { display: true, text: yLabel, color: '#64748b', font: { size: 10 } } : undefined,
    },
  },
});

const SESSION_ORDER = ['asia', 'europe', 'overlap', 'us', 'quiet'];
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/* ─── Bucket helper for mm_repeat_score ─── */
function mmBucket(v: number): string {
  if (v < 0.3)  return '0.0–0.3';
  if (v < 0.5)  return '0.3–0.5';
  if (v < 0.7)  return '0.5–0.7';
  if (v < 0.9)  return '0.7–0.9';
  return '0.9–1.0';
}
const MM_BUCKETS = ['0.0–0.3', '0.3–0.5', '0.5–0.7', '0.7–0.9', '0.9–1.0'];

function bpBucket(v: number): string {
  if (v < 0.35)  return '0.0–0.35 SHORT';
  if (v <= 0.65) return '0.35–0.65 neutral';
  return '0.65–1.0 LONG';
}
const BP_BUCKETS = ['0.0–0.35 SHORT', '0.35–0.65 neutral', '0.65–1.0 LONG'];

/* ─── Main component ─── */
export default function ScalpAnalyzerPage() {
  const [rows,    setRows]    = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const parseFile = useCallback((file: File) => {
    setLoading(true);
    Papa.parse<Row>(file, {
      header:        true,
      skipEmptyLines: true,
      complete: ({ data }) => {
        setRows(data);
        setLoading(false);
        setError('');
      },
      error: (err: { message: string }) => {
        setError(err.message);
        setLoading(false);
      },
    });
  }, []);

  const loadFromApi = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch('/api/proxy/api/scalp/export-dataset');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const text = await r.text();
      Papa.parse<Row>(text, {
        header:        true,
        skipEmptyLines: true,
        complete: ({ data }) => {
          setRows(data);
          setLoading(false);
          setError('');
        },
      });
    } catch (e) {
      setError(String(e));
      setLoading(false);
    }
  }, []);

  if (!rows.length) {
    return (
      <div className={styles.page}>
        <div className={styles.header}>
          <h1 className={styles.title}>⚡ Scalp Analyzer</h1>
          <p className={styles.subtitle}>Directional scalping dataset · mm_repeat_score · buy_pressure</p>
        </div>
        <div className={styles.uploadBox}>
          <p className={styles.uploadHint}>Load scalp positions dataset to analyse</p>
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', justifyContent: 'center' }}>
            <button className={styles.uploadBtn} onClick={loadFromApi} disabled={loading}>
              {loading ? 'Loading…' : '📡 Load from API'}
            </button>
            <button className={styles.uploadBtn} onClick={() => fileRef.current?.click()} disabled={loading}>
              📁 Upload CSV
            </button>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) parseFile(f); }}
          />
          <a
            href="https://mexc-trade-bot-production.up.railway.app/api/scalp/export-dataset"
            download
            className={styles.csvLink}
          >
            ⬇ Download scalp_dataset.csv
          </a>
          {error && <p style={{ color: '#f87171', fontSize: '12px', marginTop: '8px' }}>{error}</p>}
        </div>
      </div>
    );
  }

  /* ─── Derived stats ─── */
  const total   = rows.length;
  const tp      = rows.filter(r => r.exit_reason === 'TAKE_PROFIT');
  const sl      = rows.filter(r => r.exit_reason === 'STOP_LOSS');
  const timeout = rows.filter(r => r.exit_reason === 'TIMEOUT');
  const longs   = rows.filter(r => r.direction === 'LONG');
  const shorts  = rows.filter(r => r.direction === 'SHORT');

  const pnls    = rows.map(r => parseFloat(r.net_pnl_usdt) || 0);
  const netPnl  = r4(sum(pnls));
  const avgPnl  = r4(avg(pnls));
  const tpRate  = r2((tp.length / total) * 100);

  const holds   = rows.map(r => parseFloat(r.hold_seconds) || 0);
  const avgHold = r2(avg(holds));

  /* ─── Charts data ─── */

  // Exit reason doughnut
  const exitDonut: ChartData<'doughnut'> = {
    labels: ['TAKE_PROFIT', 'STOP_LOSS', 'TIMEOUT'],
    datasets: [{ data: [tp.length, sl.length, timeout.length],
      backgroundColor: [COLORS.green, COLORS.red, COLORS.yellow], borderWidth: 0 }],
  };

  // Direction: TP rate by LONG/SHORT
  const dirLabels = ['LONG', 'SHORT'];
  const dirTpRate = [longs, shorts].map(g => {
    const t = g.filter(r => r.exit_reason === 'TAKE_PROFIT');
    return g.length ? r2((t.length / g.length) * 100) : 0;
  });
  const dirNetPnl = [longs, shorts].map(g => r4(sum(g.map(r => parseFloat(r.net_pnl_usdt) || 0))));
  const dirBarTp: ChartData<'bar'> = {
    labels: dirLabels,
    datasets: [{ label: 'TP Rate %', data: dirTpRate, backgroundColor: [COLORS.green, COLORS.blue] }],
  };
  const dirBarPnl: ChartData<'bar'> = {
    labels: dirLabels,
    datasets: [{ label: 'Net PnL $', data: dirNetPnl,
      backgroundColor: dirNetPnl.map(v => v >= 0 ? COLORS.green : COLORS.red) }],
  };

  // Hour of day
  const hourGroups = groupBy(rows, r => r.hour_utc);
  const hours      = Array.from({ length: 24 }, (_, i) => String(i));
  const hourTp     = hours.map(h => {
    const g = hourGroups[h] ?? [];
    const t = g.filter(r => r.exit_reason === 'TAKE_PROFIT');
    return g.length ? r2((t.length / g.length) * 100) : 0;
  });
  const hourPnl = hours.map(h => r4(sum((hourGroups[h] ?? []).map(r => parseFloat(r.net_pnl_usdt) || 0))));
  const hourBarTp: ChartData<'bar'> = {
    labels: hours,
    datasets: [{ label: 'TP Rate %', data: hourTp, backgroundColor: COLORS.teal }],
  };
  const hourBarPnl: ChartData<'bar'> = {
    labels: hours,
    datasets: [{ label: 'Net PnL $', data: hourPnl,
      backgroundColor: hourPnl.map(v => v >= 0 ? COLORS.green : COLORS.red) }],
  };

  // Session
  const sessGroups = groupBy(rows, r => r.trading_session);
  const sessTp     = SESSION_ORDER.map(s => {
    const g = sessGroups[s] ?? [];
    const t = g.filter(r => r.exit_reason === 'TAKE_PROFIT');
    return g.length ? r2((t.length / g.length) * 100) : 0;
  });
  const sessPnl  = SESSION_ORDER.map(s => r4(sum((sessGroups[s] ?? []).map(r => parseFloat(r.net_pnl_usdt) || 0))));
  const sessCount= SESSION_ORDER.map(s => (sessGroups[s] ?? []).length);
  const sessBar: ChartData<'bar'> = {
    labels: SESSION_ORDER,
    datasets: [
      { label: 'TP Rate %', data: sessTp,   backgroundColor: COLORS.teal,  yAxisID: 'y'  },
      { label: 'Count',     data: sessCount, backgroundColor: COLORS.purple, yAxisID: 'y2' },
    ],
  };
  const sessOpts: ChartOptions<'bar'> = {
    responsive: true, maintainAspectRatio: true,
    plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
    scales: {
      x:  { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e293b' } },
      y:  { position: 'left',  ticks: { color: '#64748b', font: { size: 10 }, callback: v => v + '%' }, grid: { color: '#334155' } },
      y2: { position: 'right', ticks: { color: '#64748b', font: { size: 10 } }, grid: { drawOnChartArea: false } },
    },
  };

  // MM repeat score buckets
  const mmRows    = rows.filter(r => r.mm_repeat_score !== '');
  const mmGroups  = groupBy(mmRows, r => mmBucket(parseFloat(r.mm_repeat_score)));
  const mmTp      = MM_BUCKETS.map(b => {
    const g = mmGroups[b] ?? [];
    const t = g.filter(r => r.exit_reason === 'TAKE_PROFIT');
    return g.length ? r2((t.length / g.length) * 100) : 0;
  });
  const mmCount   = MM_BUCKETS.map(b => (mmGroups[b] ?? []).length);
  const mmPnl     = MM_BUCKETS.map(b => r4(sum((mmGroups[b] ?? []).map(r => parseFloat(r.net_pnl_usdt) || 0))));
  const mmBarTp: ChartData<'bar'> = {
    labels: MM_BUCKETS,
    datasets: [
      { label: 'TP Rate %', data: mmTp,    backgroundColor: COLORS.orange, yAxisID: 'y'  },
      { label: 'Count',     data: mmCount, backgroundColor: COLORS.blue,   yAxisID: 'y2' },
    ],
  };
  const mmBarPnl: ChartData<'bar'> = {
    labels: MM_BUCKETS,
    datasets: [{ label: 'Net PnL $', data: mmPnl, backgroundColor: mmPnl.map(v => v >= 0 ? COLORS.green : COLORS.red) }],
  };
  const mmDualOpts: ChartOptions<'bar'> = {
    responsive: true, maintainAspectRatio: true,
    plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
    scales: {
      x:  { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e293b' } },
      y:  { position: 'left',  ticks: { color: '#64748b', font: { size: 10 }, callback: v => v + '%' }, grid: { color: '#334155' } },
      y2: { position: 'right', ticks: { color: '#64748b', font: { size: 10 } }, grid: { drawOnChartArea: false } },
    },
  };

  // Buy pressure buckets
  const bpRows   = rows.filter(r => r.buy_pressure !== '');
  const bpGroups = groupBy(bpRows, r => bpBucket(parseFloat(r.buy_pressure)));
  const bpTp     = BP_BUCKETS.map(b => {
    const g = bpGroups[b] ?? [];
    const t = g.filter(r => r.exit_reason === 'TAKE_PROFIT');
    return g.length ? r2((t.length / g.length) * 100) : 0;
  });
  const bpPnl    = BP_BUCKETS.map(b => r4(sum((bpGroups[b] ?? []).map(r => parseFloat(r.net_pnl_usdt) || 0))));
  const bpCount  = BP_BUCKETS.map(b => (bpGroups[b] ?? []).length);
  const bpBarTp: ChartData<'bar'> = {
    labels: BP_BUCKETS,
    datasets: [
      { label: 'TP Rate %', data: bpTp,    backgroundColor: COLORS.green,  yAxisID: 'y'  },
      { label: 'Count',     data: bpCount, backgroundColor: COLORS.purple, yAxisID: 'y2' },
    ],
  };
  const bpBarPnl: ChartData<'bar'> = {
    labels: BP_BUCKETS,
    datasets: [{ label: 'Net PnL $', data: bpPnl, backgroundColor: bpPnl.map(v => v >= 0 ? COLORS.green : COLORS.red) }],
  };

  // Symbol top 10 by count
  const symGroups = groupBy(rows, r => r.symbol);
  const symTop    = Object.entries(symGroups)
    .map(([sym, g]) => {
      const t = g.filter(r => r.exit_reason === 'TAKE_PROFIT');
      return { sym, count: g.length, tpRate: r2((t.length / g.length) * 100), pnl: r4(sum(g.map(r => parseFloat(r.net_pnl_usdt) || 0))) };
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);
  const symBarCount: ChartData<'bar'> = {
    labels: symTop.map(s => s.sym),
    datasets: [{ label: 'Trades', data: symTop.map(s => s.count), backgroundColor: COLORS.blue }],
  };
  const symBarTp: ChartData<'bar'> = {
    labels: symTop.map(s => s.sym),
    datasets: [{ label: 'TP Rate %', data: symTop.map(s => s.tpRate), backgroundColor: symTop.map(s => s.tpRate >= 50 ? COLORS.green : COLORS.red) }],
  };

  /* ─── Render ─── */
  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>⚡ Scalp Analyzer</h1>
        <p className={styles.subtitle}>
          {total} trades · TP rate {tpRate}% · Net PnL {netPnl >= 0 ? '+' : ''}${netPnl} · Avg hold {r2(avgHold)}s
        </p>
        <div style={{ display: 'flex', gap: '10px', marginTop: '4px' }}>
          <button className={styles.reloadBtn} onClick={() => setRows([])}>↩ Reset</button>
          <button className={styles.reloadBtn} onClick={loadFromApi}>↻ Reload from API</button>
          <a
            href="https://mexc-trade-bot-production.up.railway.app/api/scalp/export-dataset"
            download
            className={styles.reloadBtn}
          >
            ⬇ Download CSV
          </a>
        </div>
      </div>

      {/* ── Global summary cards ── */}
      <div className={styles.summaryRow}>
        {[
          { label: 'Total',      value: total,                                     color: '#94a3b8' },
          { label: 'TP',         value: tp.length,                                 color: COLORS.green  },
          { label: 'SL',         value: sl.length,                                 color: COLORS.red    },
          { label: 'Timeout',    value: timeout.length,                            color: COLORS.yellow },
          { label: 'TP Rate',    value: `${tpRate}%`,                              color: tpRate >= 50 ? COLORS.green : COLORS.red },
          { label: 'Net PnL',    value: `${netPnl >= 0 ? '+' : ''}$${netPnl}`,    color: netPnl >= 0 ? COLORS.green : COLORS.red },
          { label: 'Avg PnL',    value: `${avgPnl >= 0 ? '+' : ''}$${avgPnl}`,    color: avgPnl >= 0 ? COLORS.green : COLORS.red },
          { label: 'LONG',       value: longs.length,                              color: COLORS.green  },
          { label: 'SHORT',      value: shorts.length,                             color: COLORS.blue   },
        ].map(({ label, value, color }) => (
          <div key={label} className={styles.summaryCard}>
            <div className={styles.summaryLabel}>{label}</div>
            <div className={styles.summaryValue} style={{ color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* ── Exit reason doughnut + Direction ── */}
      <div className={styles.chartGrid2}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Exit Reason Distribution</div>
          <div className={styles.donutWrap}>
            <Doughnut data={exitDonut} options={{ plugins: { legend: { labels: { color: '#94a3b8' } } } }} />
          </div>
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Direction — TP Rate %</div>
          <Bar data={dirBarTp} options={barOpts('TP Rate %', true)} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Direction — Net PnL $</div>
          <Bar data={dirBarPnl} options={barOpts('Net PnL $')} />
        </div>
      </div>

      {/* ── MM Repeat Score analysis (key feature) ── */}
      <div className={styles.sectionTitle}>📊 MM Repeat Score (key entry signal)</div>
      <div className={styles.chartGrid2}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>MM Score Bucket → TP Rate % vs Count</div>
          <Bar data={mmBarTp} options={mmDualOpts} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>MM Score Bucket → Net PnL $</div>
          <Bar data={mmBarPnl} options={barOpts('Net PnL $')} />
        </div>
      </div>

      {/* ── Buy Pressure analysis ── */}
      <div className={styles.sectionTitle}>📈 Buy Pressure (directional signal)</div>
      <div className={styles.chartGrid2}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Buy Pressure → TP Rate % vs Count</div>
          <Bar data={bpBarTp} options={mmDualOpts} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Buy Pressure → Net PnL $</div>
          <Bar data={bpBarPnl} options={barOpts('Net PnL $')} />
        </div>
      </div>

      {/* ── Session / Hour ── */}
      <div className={styles.sectionTitle}>🕐 Time Analysis</div>
      <div className={styles.chartGrid2}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Trading Session — TP Rate % vs Count</div>
          <Bar data={sessBar} options={sessOpts} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Trading Session — Net PnL $</div>
          <Bar
            data={{ labels: SESSION_ORDER, datasets: [{ label: 'Net PnL $', data: sessPnl, backgroundColor: sessPnl.map(v => v >= 0 ? COLORS.green : COLORS.red) }] }}
            options={barOpts('Net PnL $')}
          />
        </div>
        <div className={`${styles.chartCard} ${styles.chartWide}`}>
          <div className={styles.chartTitle}>Hour UTC — TP Rate %</div>
          <Bar data={hourBarTp} options={barOpts('TP Rate %', true)} />
        </div>
        <div className={`${styles.chartCard} ${styles.chartWide}`}>
          <div className={styles.chartTitle}>Hour UTC — Net PnL $</div>
          <Bar data={hourBarPnl} options={barOpts('Net PnL $')} />
        </div>
      </div>

      {/* ── Top symbols ── */}
      <div className={styles.sectionTitle}>🔝 Top 10 Symbols by Trade Count</div>
      <div className={styles.chartGrid2}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Symbol — Trade Count</div>
          <Bar data={symBarCount} options={barOpts('Trades')} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Symbol — TP Rate %</div>
          <Bar data={symBarTp} options={barOpts('TP Rate %', true)} />
        </div>
      </div>

      {/* ── Symbol stats table ── */}
      <div className={styles.sectionTitle}>📋 Per-Symbol Stats (top 20 by count)</div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>Symbol</th>
              <th className={styles.th}>Count</th>
              <th className={styles.th}>LONG</th>
              <th className={styles.th}>SHORT</th>
              <th className={styles.th}>TP</th>
              <th className={styles.th}>SL</th>
              <th className={styles.th}>Timeout</th>
              <th className={styles.th}>TP Rate</th>
              <th className={styles.th}>Net PnL</th>
              <th className={styles.th}>Avg Hold</th>
              <th className={styles.th}>Avg MM</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(symGroups)
              .map(([sym, g]) => {
                const tp2    = g.filter(r => r.exit_reason === 'TAKE_PROFIT').length;
                const sl2    = g.filter(r => r.exit_reason === 'STOP_LOSS').length;
                const to2    = g.filter(r => r.exit_reason === 'TIMEOUT').length;
                const lng    = g.filter(r => r.direction === 'LONG').length;
                const sht    = g.filter(r => r.direction === 'SHORT').length;
                const pnl2   = r4(sum(g.map(r => parseFloat(r.net_pnl_usdt) || 0)));
                const hold2  = r2(avg(g.map(r => parseFloat(r.hold_seconds) || 0)));
                const mmVals = g.map(r => parseFloat(r.mm_repeat_score)).filter(v => !isNaN(v));
                const mmAvg  = mmVals.length ? r2(avg(mmVals)) : null;
                return { sym, count: g.length, tp: tp2, sl: sl2, timeout: to2, lng, sht, pnl: pnl2, hold: hold2, mmAvg, tpRate: r2((tp2 / g.length) * 100) };
              })
              .sort((a, b) => b.count - a.count)
              .slice(0, 20)
              .map(({ sym, count, tp: tp2, sl: sl2, timeout: to2, lng, sht, pnl, hold, mmAvg, tpRate }) => (
                <tr key={sym} className={styles.row}>
                  <td className={styles.td}><span className={styles.sym}>{sym}</span></td>
                  <td className={styles.td}>{count}</td>
                  <td className={styles.td} style={{ color: COLORS.green }}>{lng}</td>
                  <td className={styles.td} style={{ color: COLORS.blue }}>{sht}</td>
                  <td className={styles.td} style={{ color: COLORS.green }}>{tp2}</td>
                  <td className={styles.td} style={{ color: COLORS.red }}>{sl2}</td>
                  <td className={styles.td} style={{ color: COLORS.yellow }}>{to2}</td>
                  <td className={styles.td}>
                    <span style={{ color: tpRate >= 50 ? COLORS.green : COLORS.red }}>{tpRate}%</span>
                  </td>
                  <td className={styles.td}>
                    <span style={{ color: pnl >= 0 ? COLORS.green : COLORS.red }}>
                      {pnl >= 0 ? '+' : ''}${pnl}
                    </span>
                  </td>
                  <td className={styles.td}>{hold}s</td>
                  <td className={styles.td}>{mmAvg ?? '—'}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
