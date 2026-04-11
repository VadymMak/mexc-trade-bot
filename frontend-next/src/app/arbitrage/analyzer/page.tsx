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

/* ─── Main component ─── */
export default function AnalyzerPage() {
  const [rows, setRows] = useState<Row[] | null>(null);
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

  return <Dashboard rows={rows} onReset={() => setRows(null)} />;
}

/* ─── Dashboard ─── */
function Dashboard({ rows, onReset }: { rows: Row[]; onReset: () => void }) {
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
