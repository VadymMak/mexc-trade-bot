# BLUEPRINT — the plan

**This file is the plan. Everything else is appendix.** Edited in place, never appended to; history lives in git.
If something here is stale, fix it here — do not write a new status document.

Last updated: 2026-08-26 · Supersedes `CURRENT_STATUS.md`

---

## 0. Why this file exists

Three different kinds of knowledge were being stored the same way — as dated prose — which is what makes work
feel like random motion: **the open-questions list gets buried inside narratives, so whenever something breaks,
the break becomes the plan.**

| kind | belongs in |
|---|---|
| **Settled facts** — won't change | **§3**, written once, never re-derived |
| **Open decisions** — the actual plan | **§2**, as gates |
| **Events** — disposable after a week | Brain summaries / git |

## 1. The goal

Find a **direction-neutral yield** that survives honest measurement — real fills, real fees, real tail risk — and
determine **at what size it is worth operating**, judged as a premium over the euro risk-free rate (~2.25%) after
tax and off-ramp, never as a headline APR.

Not: predicting price. Not: a bigger backtest.

---

## 2. Decision gates

A question without a pre-committed consequence is a hobby. Each gate states what would answer it **and what we do
for each answer**, decided before the data arrives.

### G1 — Does the carry (perp funding) yield hold across regimes?
- **Status:** open. ~130 epochs, one week, one regime. 11.4% net APR in the resting regime; 33–36% during a
  funding wave.
- **Answered by:** accumulating settlements across generations for several more weeks. Gaps excluded; no unbroken
  window needed. **No repair shortens this.**
- **≥ ~10% net APR persists →** proceed to G2. **Collapses to the resting state →** carry is a deposit with
  exchange risk; park it.

### G2 — How much capital can a book absorb?
- **Status:** open, and unanswerable by observation. Per-name depth measured between **$584 (top-5)** and
  **$19,314 (50-level)** — a 33× spread.
- **Answered by:** a minimal live execution test measuring realised slippage, sized so the maximum loss is worth
  less than the information.
- **Capacity × premium < cost of operating →** park regardless of G1. **This is the gate to any real capital.**

### G3 — Is any of #2 / #3 / #4 both orthogonal and payable?
- **Status:** all three collecting since 2026-08-24. **First look done — and it reorders the candidates.**
  - **#2 dated basis is the only one that does not rest at a default.** Median 2.7–4.5% annualised, tight across
    five independent venues — the tightness across venues *is* the evidence it is a real price.
  - **#3 lending rests at defaults, same signature as funding** — kucoin and okx supply rates have **one distinct
    value in 611 observations**. Only 6 of 14 series are the supply side you can actually earn.
  - **#4 LP median is 0.19%, below the risk-free anchor**, with a 9× mean/median ratio; only 14.55% of
    observations clear 2.25%.
- **Answered by:** the §4 yardstick, per the readable-on dates in §5.
- **None clears the yardstick →** the answer to the project is "no yield here", reached on evidence.
- **Priority follows the evidence: #2 first.**

### G4 — Can a book be held at a concentration that survives a bad event?
- **Status:** partially answered and currently failing. Largest carry position ~24% against a **6.7%** line;
  **15 names needed** for a p95 death not to be a portfolio event, **77** for a line item; **15–16 of 153 pass.**
- **New constraint from #4:** diversifying across LP pools **increases** the number of distinct contract systems
  you are exposed to (13 chains, ~20 projects). **Diversification and contract-risk reduction pull against each
  other** — the CEX framework never had to model this.
- **Answered by:** a per-name weight cap (measure yield given up at 6.7% / 10% / 12.5%), then re-count the
  passing set **after** the mean→median fix, which is predicted to shrink it.
- **Passing set below ~15 →** cannot be diversified enough to run, whatever the yield.

### G5 — What happens to live positions when the host dies?
- **Status:** open. Two unclean stops in three days; no UPS; a live bot would be blind 16–32 min.
- **Answered by:** a choice, not data — UPS for clean shutdown, an external watchdog that alerts or flattens on
  silence, a documented restart-into-known-state procedure, or a different live host.
- **Nothing goes live until this is written down.**

---

## 3. Settled — do not re-derive

Edit a line if a new result contradicts it; never add a second.

**Yield structure**
- **Perp funding rests at a venue default** — median exactly `5e-05` on gate, mexc, bitget, bybit, kucoin, okx;
  ~5–15% of names pay above it, and that tail rotates. **Adding venues does not enlarge the opportunity set.**
- **Pinning is sticky** — after 2 settlements at/below default, 84% are still there 21 settlements later.
- **CEX lending rests at defaults too** — kucoin USDC/USDT supply 3.650% (raw exactly 0.0001/day), okx 3.000%
  (raw exactly 0.03), one distinct value in 611 observations. Aave moves continuously; CEX does not.
- **Dated basis does NOT rest at a default** — p50 by venue: gate 2.72 / okx 3.72 / binance 3.73 / bybit 4.40 /
  deribit 4.53. Five independent venues agreeing is the evidence.
- **Stable-LP fee yield is right-skewed 9×** — clean set (116 pools) median **0.192%**, mean 1.722%.

**Costs**
- **Dated basis cost is the futures leg** — 92–97% of the roundtrip. Median roundtrip: binance 6.60 / gate 18.41 /
  bybit 20.58 / okx 23.31 bps. **At a 3.7% median, a 30-day contract grosses ~30 bps against that cost; a 7-day
  contract grosses ~7 bps and cannot cover it.** Dated basis is a longer-hold trade or it is nothing.
- **Aave's high rate is the price of being locked in** — USDC sat at 12.54% for 23.1% of observations, and at
  those moments utilisation was **0.99975** (supply $2.031bn ≈ borrows $2.031bn). When utilisation fell to 92.4%
  the rate went to 3.71%. **The high rate and the inability to exit are the same variable.**
- **Carry engine cost ÷ income: 239% → 27%** after the Phase-3b fixes; rebalances 491/day → 3 in 21.5h; 0 errors.

**Risk**
- **Instrument death** ~12.3%/yr batch-adjusted (MEXC 24.3% vs Gate 11.3%); LGD mean 7.56% / median 3.64% /
  **p95 66.5%**, the tail resting on one observation (VANRY).
- **Death warning fails where it matters** — already-wide names give no advance signal (0.91× vs control).
- **The modelled funding rate is 2× optimistic** — mean of a right-skewed series; **realised ≈ 50% of modelled**
  (n=13, CI 0.34–0.83); median-14d is ~unbiased (0.93).
- **Durability:** `data_checksums=on`, `fsync=on`, `full_page_writes=on`; **0 checksum failures for the cluster's
  whole life.** Corruption is not the risk; the single copy is.
- **Arbitrage is dead, proven** — mark-price sim 95% win / +22 bps vs honest executable 0.3% win / **−214 bps**
  over 875 trades. Taker crossing cost was the entire loss.
- **8 of 30 ёрш candidates carried manufactured tape, and the selection rule found them by construction** — it
  banded by 24h volume on a venue with a ~$100k reported-volume floor.
- **The ёрш "farm dumb market-makers" premise also failed on measurement, independently of the universe
  problem** — mid-markout was positive on *every* candidate (adverse selection everywhere), and the wide quoted
  spreads are empty: 18–178 ticks against ~$70/min of actual flow, so you are undercut by one tick and there is
  no size to collect.
- **The queue-aware maker-fill simulator was BUILT and RUN (2026-08-15, ~21.7 h of L2, 5 candidates) —
  it is not outstanding work.** Verdict `ERSH_QUEUE_SIM_VERDICT.md`: ёрш is a **queue game, not a latency
  game, and a non-colocated bot loses it.** Front of queue is worth +12 to +14 bps per quoting cycle (LA, ONE);
  the back of the queue — the only place we can actually stand — is **−0.7 to −16.7 bps**. **Latency is
  irrelevant: 50 ms and 1000 ms give the same answer** (LA −0.68 vs −0.85), so a faster link buys nothing.
  Also: **`mexc ONE_USDT` was never locked-1-tick** and should not have been in the candidate set;
  `gate MYX_USDT` is too thin to judge (90.8% of cycles produced no fill).
  **So ёрш has three independent reasons to stay parked, not one.**
- **Maker perp-perp convergence earns ~zero after adverse selection** — measured on **661,177 real passive
  fills**: MEXC coins take a median 20.87 bps half-spread at fill and keep **−1.29 bps** 60 s later. "We earn
  the spread instead of paying it" is empirically false on these books.

**Technical gotchas — all still true**
- MEXC needs `sub.depth.full`; plain `sub.depth` streams unsorted diffs (this made ONE_USDT look like 444 bps
  against a real 15 bps).
- **Markout must be measured from book mid, not trade price** — the sign flipped on all 27 candidates.
- "Locked-1-tick" is time-varying — mexc ONE widened ~6× within a day.
- Trade size is **contracts**; apply the multiplier.
- `/api/carry/export-dataset` is a memory bomb.
- Server work happens only in the SSH panel.
- **Collector semantics changed 2026-08-19T12:59:25Z** (funding-interval fix, and with it the "store inputs
  beside outputs" principle the three new collectors follow). Any analysis crossing that timestamp needs it.
  Depth basket went 129→153 at 2026-08-19T13:28:12Z.
- `LESSONS_LEARNED.md` **does not exist** — the Brain skill `trading-edge-lessons` is the only copy. Recorded so
  the next session stops hunting for it.
- Dead deployment artifacts, ignore: `docker-compose.yml`, `Dockerfile`, `ecosystem.config.js`, `Procfile`,
  `railpack-plan.json`.

## 4. The yardstick — same seven questions for every candidate

Never compare headline APRs. **Report medians and distributions, not means** — the 2× estimator error came from
exactly that.

1. Realised **from receipts**, with the realised ÷ modelled ratio and the epoch count.
2. **Cost ÷ income** on actual turnover.
3. Tail priced: what kills it, base rate, loss given the event.
4. Concentration: instruments needed so one bad event is not a portfolio event.
5. Capacity before the yield degrades.
6. **Net-net as percentage points over ~2.25%**, after cost, tail, tax, off-ramp.
7. Regime stamp — everything so far is one bear-market sample.

**Current scores — nothing yet answers item 1 fully:**

| | #2 basis | #3 lending | #4 LP |
|---|---|---|---|
| 1 realised | partial — 3 Deribit dailies observed to 6 s before expiry, gross only | no — marks | no — source-supplied mark |
| 2 cost÷income | yes for 4 of 5 venues; Deribit 100% NULL | partly — utilisation *is* the cost | no — fee tier on 34% of rows, gas absent |
| 3 tail | no | partly — the 99.97% lockup is observed | no |
| 4 concentration | partly — 136 instruments but only ~14 coins | **no — 6 earnable series, 2 assets** | partly — but the above-anchor tail is 28 pools |
| 5 capacity | no — OI NULL on binance/okx | partly | partly — tail pools are $1–20M, which is the answer |
| 6 net-net | no | no — pinned 3.00–3.65% is visibly near the anchor | no — 0.19% median is below it |

## 5. In flight

| what | state | readable when |
|---|---|---|
| paper carry bot, **generation 11** (2026-08-26 06:29:55.328555Z, git 7719286) | running, paper-mode intact | continuously; G1 needs weeks |
| #2 dated basis (`mexc-basis`) | 5 venues, 136 instruments, 40 expiries | **narrow now**; broad multi-venue convergence **2026-08-28**; 4–6 cycles ≈ **2026-10-02**; first quarterly **2026-09-25** |
| #3 lending (`mexc-lending`) | 5 sources, 14 series, 2 assets | Aave mean to ±0.05pp ≈ **2026-09-12**; **CEX series are constants — no date** |
| #4 stable LP (`mexc-lp`) | 13 chains, ~203 pools (116 clean) | first full Mon–Sun **2026-08-31**; 30-day mean ≈ **2026-09-23** |
| database backup | **implemented, verified restorable, running nightly — still on the same disk** | — |

**Topology:** home Ubuntu server `trading-server`, reached over Tailscale (100.112.227.114), **systemd
only — no Docker, no nginx, no Railway.** Local PostgreSQL `trading_bot`, role `mexc`.

**Live inventory:** 11 active units — backend, frontend, carry-collector, carry-depth, carry-paper,
venue-funding, ersh-tape, ersh-l2, basis, lending, lp — plus 3 timers (2 backup, 1 diskwatch).
`mexc-researcher` and `mexc-carry-tape` inactive and disabled, both by design.
Row counts **as of 2026-08-26 18:40Z** (they only grow; the stamp is what makes them readable):
`funding_basis_snapshots` 9,768,030 · `carry_book_l2` ~148.7M · `ersh_book_l2` 24,952,570 ·
`tape_prints` 7,655,391 · `basis_snapshots` 97,668 · `lp_snapshots` 24,321 · `lending_snapshots` 7,722.

**Blocking, two minutes of work:** the backup ships nowhere. On the Mac — Remote Login ON,
`mkdir -p ~/mexc-backups`, append the `mexc-backup@trading-server` ed25519 key to `~/.ssh/authorized_keys`, then
`SHIP_ENABLED=1` / `SHIP_REQUIRED=1` in `/etc/mexc-backup.conf`. **Until then the backup sits on the disk it
protects.**

**Standing watch:** the listing-obligation hypothesis is **untested, not refuted** — the earlier "refuted" call
was an artifact (all 30 ёрш symbols' first prints fall inside a 273.5 s window = collector start). Testable
prospectively via `research/queries/new_listing_watch.sql`.

**Data-gap rule:** `INTERMITTENT` fires on all ~1,197 symbols for any lifecycle audit whose window is under
~11 h and spans the 2026-08-26 gap (~5 h for the 08-24 gap). `DISAPPEARED` cannot misfire — it needs
`last_seen > 24 h`. **Any lifecycle audit must exclude the gap windows or use a window longer than ~11 h.**

**Known collector defects, unfixed and worth knowing before they cost anything:** `annualized_pct` is NULL for
100% of basis observations at dte < 0.5 d — i.e. absent through the convergence window you most want;
`roundtrip_spread_bps` is NULL for 100% of Deribit and 82% at dte 0.5–2 d, so cost is missing where the trade
goes on; **Deribit prices index vs mark, a modelled pair, so it has no executable number at all**; binance and
okx have `venue_basis_raw` and `future_oi` 100% NULL.

## 6. Parked — with the reason, so it is not re-proposed

- **Gate↔MEXC arbitrage** — −214 bps over 875 executable trades.
- **Listing event edge** — negative even with perfect direction foresight at +5m.
- **Delisting convergence** — real and systematic, but a one-sided perp discount, not neutral convergence.
- **Fast funding-floor exit rule** — 8:1 against; ~60% false exits on the population it would fire on.
- **ёрш / maker spread collection** — universe invalid at the selection step; would need a rule that does not
  band by reported volume.
- **Multi-venue as diversification** — same factor everywhere.

## 7. Working rules

1. **Every prompt names the gate it serves in its first line** — G1…G5, or explicitly `FIX` / `HOUSEKEEPING`.
   A prompt serving no gate and not labelled a fix is drift.
2. **Every prompt's final step: update this file** — which gate moved, what became settled, what changed in §5.
   The side holding the data maintains the plan; this is also the bridge between chats.
3. **Session sign-off names the gates *not* touched.** This is the anti-drift mechanism.
4. **Fixes are labelled and bounded** — say whether the break *blocks a gate*. The rest is where drift lives.
5. **No dated snapshot documents.** A dated document is written only when a gate closes — that is a verdict.
6. **Project memory holds durable facts and rules only, never current state.** State lives in §5 and is edited.
7. **Brain is for "what happened" and cross-chat catch-up, not the plan.**
