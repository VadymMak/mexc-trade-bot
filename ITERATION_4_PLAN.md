# 🗺️ Iteration 4 — Plan

> Historical as of 2026-08-26 — superseded by BLUEPRINT.md.
**Created:** 2026-04-11  
**Status:** 🟡 In Progress  
**Goal:** Validate fixes → tune strategy → expand exchanges → prepare for live trading

---

## 📍 Current State (after Iteration 3)

### ✅ Fixes deployed (need validation):
- `ZSCORE_REVERT_MIN_HOLD_SECONDS = 120` — blocks noisy early ZR exits
- `TRADING_EXCHANGES = "gate,mexc"` — removes phantom binance/bybit pairs
- Symbol lifecycle: `TESTING → APPROVED / BLACKLISTED` (auto after 7 days / 30 trades)

### ✅ New infrastructure:
- `/arbitrage/analyzer` — frontend CSV analysis page with clean data toggle
- `?clean=true` on export endpoint — server-side dirty data filter
- `symbol_states` table in Neon DB
- `SymbolEvaluator` — auto-blacklists symbols with tp_rate < 30% or net_pnl ≤ -$1

### 📊 Last dataset snapshot (2026-04-11, dirty data):
- 9,693 trades, net PnL = -$74.61
- Root causes: ZSCORE_REVERT (60.5% exits, 8.4% WR) + phantom binance spreads
- Top losers: STO (-$19.9), NOM (-$15.5), ARIA (-$14), BULLA (-$14), CHILLGUY (-$12)
- Top winners: MAGMA (+$6.3), AKE (+$5.2), AIOT (+$2.4)

---

## 🔲 Phase A — 7-Day Clean Data Collection
**Duration:** 7 calendar days (passive — no code changes)  
**Goal:** Collect clean dataset with both fixes active

- [ ] Run bot continuously on Railway (gate+mexc only, ZSCORE min hold 120s)
- [ ] Symbol evaluator runs silently, starts building TESTING stats
- [ ] After 7 days: download Clean CSV (`?clean=true`) and open in analyzer
- [ ] Key metrics to check:
  - ZSCORE_REVERT rate (target: < 30%, was 60.5%)
  - TP rate (target: > 40%, was 29.8%)
  - Net PnL (target: > 0)
  - First APPROVED symbols should appear

**Done when:** 7 days passed + at least 500 clean trades collected

---

## 🔲 Phase B — Analyze & Tune Spread Threshold
**Duration:** 1 day (after Phase A data)  
**Goal:** Based on clean data — raise MIN_SPREAD_PCT to filter low-quality signals

**Background:** Klevtsov insight: real arb works at 3-7% deviation.  
Our data: >2% spread bucket had best absolute PnL. 0.3-0.5% bucket: fees eat everything.

- [ ] Open clean CSV in analyzer — check WR by spread bucket
- [ ] If <0.5% bucket still negative → raise `MIN_SPREAD_PCT` from current to 0.5-0.6%
- [ ] Run 48h test with new threshold — compare trade count vs PnL trade-off
- [ ] Update config.py: `MIN_SPREAD_PCT`
- [ ] Commit + push

**Done when:** clean dataset confirms optimal MIN_SPREAD_PCT value

---

## 🔲 Phase C — MM Robot Detector
**Duration:** 2-3 days  
**Goal:** Detect market maker robot patterns in trade tape → score symbol quality

**Klevtsov insight:** "MM hits with the same volume repeatedly" = predictable, exploitable.  
If bot detects MM robot → spread is stable → arb is more reliable.

**Implementation:**
- [ ] Add `mm_robot_score` to `FlowTracker` in `market_flow.py`
  - Window: last 20 trades per symbol
  - Pattern: 3+ consecutive trades with qty within 5% of each other = robot detected
  - Score: 0.0–1.0 (ratio of "robot-like" clusters in window)
- [ ] Save `mm_robot_score` to `paper_positions` table (new column)
- [ ] Add to CSV export and analyzer page
- [ ] Analyze: do trades with `mm_robot_score > 0.5` have higher TP rate?

**Done when:** mm_robot_score collected for 200+ trades and correlation with TP rate visible

---

## 🔲 Phase D — Symbol States Monitoring in Frontend
**Duration:** 1 day  
**Goal:** See TESTING/APPROVED/BLACKLISTED symbols directly in the Arbitrage UI

- [ ] Add "Symbols" tab to `/arbitrage` page
- [ ] Fetch from `GET /api/arbitrage/research/symbol-states`
- [ ] Show 3 sections: APPROVED (green), TESTING (yellow, with progress bar), BLACKLISTED (red, with retest date)
- [ ] Show per-symbol stats: trades, tp_rate, net_pnl, days in test

**Done when:** page shows live symbol states from Neon DB

---

## 🔲 Phase E — Add KuCoin + HTX as Price Collectors
**Duration:** 3-4 days  
**Goal:** More exchange pairs → more arbitrage opportunities (5x more pair combos with 4 exchanges)

**Strategy:** Add as price-only collectors first (no live trading on them yet)

- [ ] Create `KuCoinCollector` (WebSocket, perpetual futures prices)
- [ ] Create `HTXCollector` (WebSocket, perpetual futures prices)  
- [ ] Add to `SpreadMatrix` — new spreads: gate↔kucoin, mexc↔kucoin, gate↔htx, mexc↔htx
- [ ] Keep `TRADING_EXCHANGES = "gate,mexc"` — new exchanges are monitoring only
- [ ] After 7 days of data: check if gate↔kucoin or mexc↔kucoin spreads are real (non-phantom)
- [ ] If validated: add to `TRADING_EXCHANGES`

**Note:** Consider ccxt for unified WS interface to speed up collector development.

**Done when:** KuCoin + HTX prices flowing into SpreadMatrix, new pairs visible in frontend

---

## 🔲 Phase F — Liquidation Protection (pre-live requirement)
**Duration:** 2 days  
**Goal:** Mandatory safety mechanism before any live trading

**Risk scenario:** One leg (e.g. Gate long) gets liquidated while MEXC short stays open.
Result: unhedged directional position = unlimited loss.

- [ ] Add `liquidation_guard` to paper trader and future live executor
- [ ] On any position: if one leg PnL drops below -80% of deal size → emergency close both legs
- [ ] Alert mechanism: log + push notification when triggered
- [ ] Test with simulated liquidation scenario

**Done when:** liquidation protection tested and confirmed working

---

## 🔲 Phase G — Live Trading Preparation
**Duration:** TBD (after Phases A-F complete)  
**Prerequisites:**
- ✅ Clean dataset showing positive net PnL over 7+ days
- ✅ At least 10 APPROVED symbols with tp_rate > 50%
- ✅ MM robot detector collecting data
- ✅ Liquidation Protection implemented
- ✅ Min spread threshold tuned

**Steps:**
- [ ] Start with $20-50 real capital (micro-size, same as paper $10/trade)
- [ ] Gate + MEXC only (proven exchanges)
- [ ] Strict position limit: max 3 open positions simultaneously
- [ ] Daily P&L review for first 2 weeks

---

## 📊 Success Metrics per Phase

| Phase | Key Metric | Target |
|-------|-----------|--------|
| A | Net PnL (clean 7d) | > $0 |
| A | ZSCORE_REVERT rate | < 30% |
| A | APPROVED symbols | ≥ 5 |
| B | TP rate at new threshold | > 50% |
| C | mm_robot_score vs TP correlation | r > 0.3 |
| E | New pairs with real spreads | ≥ 10 |
| G | Live PnL week 1 | > -$5 |

---

## 🧠 Key Insights (from NotebookLM / Klevtsov)
1. "90% success = find the token giving money RIGHT NOW" → scanner + 7-day auto evaluation is the right approach
2. MM robot pattern (same qty 3+ times) = stable spread = exploitable → implement mm_robot_score
3. Real arb entry: 3-7% deviation (our current 0.3-0.5% = too noisy for fees) → raise threshold
4. Liquidation Protection is non-negotiable before real money
5. ccxt for adding new exchanges (scanner layer) — not for execution (too slow)
6. Micro-volumes ($2) to not scare MM algorithm — already doing this ✅
