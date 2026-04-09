'use client';

import { useState, useEffect, useCallback } from 'react';
import { usePolling } from '@/hooks/usePolling';
import styles from './BotControl.module.css';

interface MetricsResponse {
  realized_pnl?: Record<string, number>;
}

interface ProviderResponse {
  mode?: string;
}

function sumPnL(metrics: MetricsResponse | null): number {
  if (!metrics?.realized_pnl) return 0;
  return Object.values(metrics.realized_pnl).reduce((acc, v) => acc + v, 0);
}

function fmtPnL(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `PnL: ${sign}$${value.toFixed(2)}`;
}

export default function BotControl() {
  const [running, setRunning] = useState<boolean | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const [pnl, setPnl] = useState<number>(0);
  const [busy, setBusy] = useState(false);

  // Load provider/mode once on mount
  useEffect(() => {
    fetch('/api/proxy/api/config/provider')
      .then((r) => (r.ok ? r.json() : null))
      .then((data: ProviderResponse | null) => {
        if (data?.mode) setMode(data.mode.toUpperCase());
      })
      .catch(() => {});
  }, []);

  // Poll metrics every 10s
  const fetchMetrics = useCallback(async () => {
    const r = await fetch('/api/proxy/api/strategy/metrics');
    if (!r.ok) return;
    const data: MetricsResponse = await r.json();
    setPnl(sumPnL(data));

    // Derive running state from open_positions or entries
    const full = data as MetricsResponse & { open_positions?: Record<string, number> };
    if (full.open_positions !== undefined) {
      setRunning(Object.values(full.open_positions).some((v) => v > 0));
    }
  }, []);

  usePolling(fetchMetrics, 30_000);

  const toggle = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const endpoint = running
        ? '/api/proxy/api/strategy/stop-all'
        : '/api/proxy/api/strategy/start';
      const body = running ? { flatten: false } : { symbols: [] };
      await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setRunning((prev) => !prev);
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  };

  const pnlClass =
    pnl > 0 ? styles.pnlPositive : pnl < 0 ? styles.pnlNegative : styles.pnlNeutral;

  return (
    <div className={styles.wrapper}>
      {/* Mode badge */}
      {mode && (
        <span
          className={`${styles.modeBadge} ${
            mode === 'LIVE' ? styles.modeLive : styles.modePaper
          }`}
        >
          {mode}
        </span>
      )}

      {/* Toggle */}
      <label className={styles.toggle}>
        <input
          type="checkbox"
          className={styles.toggleInput}
          checked={running ?? false}
          disabled={busy || running === null}
          onChange={toggle}
        />
        <span className={styles.toggleLabel}>
          {running === null ? '...' : running ? 'BOT ON' : 'BOT OFF'}
        </span>
      </label>

      {/* PnL */}
      <span className={`${styles.pnl} ${pnlClass}`}>{fmtPnL(pnl)}</span>
    </div>
  );
}
