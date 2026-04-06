'use client';

import { useState } from 'react';
import SpreadTable from '@/components/SpreadTable/SpreadTable';
import styles from './page.module.css';

export default function TradingBoardPage() {
  const [search, setSearch] = useState('');
  const [minSpreadPct, setMinSpreadPct] = useState(0);

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>Spread Monitor</h1>
        <p className={styles.subtitle}>
          Live scanner — top symbols by spread, updated every 2s
        </p>
      </div>

      {/* Filter bar */}
      <div className={styles.filterBar}>
        <span className={styles.filterLabel}>Filter</span>

        <input
          className={styles.searchInput}
          type="text"
          placeholder="Search symbol…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />

        <div className={styles.divider} />

        <span className={styles.filterLabel}>Min spread</span>
        <input
          className={styles.spreadInput}
          type="number"
          min={0}
          step={0.1}
          placeholder="0.00 %"
          value={minSpreadPct === 0 ? '' : minSpreadPct}
          onChange={(e) => setMinSpreadPct(Number(e.target.value) || 0)}
        />
        <span className={styles.filterLabel}>%</span>
      </div>

      {/* Main table */}
      <div className={styles.tableCard}>
        <SpreadTable search={search} minSpreadPct={minSpreadPct} />
      </div>
    </div>
  );
}
