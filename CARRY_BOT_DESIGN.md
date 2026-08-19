# CARRY BOT — DESIGN (Phase 3)

**Status: PAPER ONLY.** No trading keys exist in this system, no order-placement code path
exists, and the live executor is a stub that raises. This document is the thing to argue with
*before* any of that changes.

Implementation: `researcher/app/carry/bot/`. Service: `mexc-carry-paper`.
Evidence base: [`CARRY_CAPACITY_PORTFOLIO.md`](CARRY_CAPACITY_PORTFOLIO.md) (61-name worst-hour
study) and the run-2 depth collection now covering all 129 names.

---

## 0. WHAT THIS BOT IS BETTING ON, IN ONE PARAGRAPH

Long spot + short perp on the same coin is delta-neutral. The short perp collects funding
whenever funding is positive. The measured, capacity-adjusted, interval-corrected expectation is
**~31–33% net APR on capital at €1,000**, decaying to ~17% by €20k, on a 30-day hold at L=2×
with maker fees. The bot exists to find out whether that number survives contact with reality
over days of live accrual — **not** to prove it. The paper track record is the falsification
test. If paper accrual comes in materially under model, we do not go live.

Three things are known to be able to break it, and each has an explicit rule below: funding
flips negative, the spot book thins out at the hour we need to exit, and the venue we hold
positions on stops honouring withdrawals.

---

## 1. COMPONENTS

### (a) Selector — `bot/selector.py`

Picks the basket each cycle from `funding_basis_snapshots` + `carry_book_l2`. A name is a
candidate only if it passes **every** gate:

| gate | rule | why |
|---|---|---|
| funding sign stability | ≥ 80% of epochs positive over the lookback | one big positive print does not make a carry |
| sample size | ≥ 12 real funding epochs | fewer and the mean is noise |
| **corrected interval** | interval fetched live per name, never the stored column | `funding_interval_hours` is **8 in all 7.75M rows** — a hardcode. 95/129 names actually settle 4h, so trusting it understates APR ~2× and mis-times every accrual |
| spot leg exists | `spot_bid > 0` and a spot order book is present | no spot leg, no carry |
| spread | median perp + spot spread ≤ `MAX_RT_SPREAD_BPS` | wide quotes eat the funding |
| basis sanity | \|mean basis\| ≤ `MAX_BASIS_BPS` | a permanently dislocated basis is a broken pair, not an opportunity |
| **worst-hour depth** | worst-hour capacity at the size cap, not median | see §2 |
| net APR after costs | capacity-adjusted net-on-capital > `MIN_NET_APR` | a name that only works before costs does not work |

Ranking is by **capacity-adjusted net APR on capital**, then greedy allocation subject to the
per-name and per-venue caps. Selection re-runs every `SELECT_EVERY_MIN`; existing positions are
not churned just because the ranking moved — a held name is only exited by a risk rule.

### (b) Executor — `bot/executor.py`

Opens **both legs or neither**. Long spot, short perp, same notional, **maker** intent, low
leverage.

- `PaperExecutor` — the only implementation that runs today. Prices both legs by **walking the
  real `carry_book_l2` book** (VWAP from the touch), so the entry cost is the honest executable
  one, not a mid-price fantasy. This is the lesson from the arbitrage post-mortem, where
  mark-price simulation showed 95% win and the book-walked truth was 0.3%.
- `LiveExecutor` — **stub that raises `NotImplementedError`**. It is the seam, not a feature.
- **Leg-risk is modelled, not assumed away**: the paper executor charges both legs' slippage and
  records the entry basis, so a one-legged fill is representable rather than silently perfect.

### (c) Neutrality manager — `bot/risk.py`

Tracks `delta_usd = spot_qty·spot_price − perp_qty·perp_mark` per name. The two legs drift apart
as the basis moves even when quantities are unchanged. Rebalances when
`|delta| / notional > REBALANCE_DELTA_PCT`, and logs every drift observation so the paper record
shows how much rebalancing a live bot would actually have done.

### (d) Risk manager — `bot/risk.py`

Evaluated every cycle for every open position; see §2 for the rules. Every trigger writes an
event to the runner log whether or not it fires an exit, so a rule that never fires is visible
as *tested and quiet* rather than as *possibly not wired up*.

### (e) Funding tracker — `bot/funding.py` (in `selector.py`/`main.py`)

At each **real** funding epoch boundary, accrues `notional × funding_rate` using the
funding_rate actually observed nearest that boundary, and records it against the **modelled**
rate used at selection. `realised_funding_usd` vs `modelled_funding_usd` per position is the
core diagnostic: if realised persistently lags modelled, the selector's mean is biased and the
31–33% model is wrong.

### (f) Watchdog / reconnect — hard requirement

**Non-negotiable, and the reason it is listed as a component rather than a detail.** On
2026-08-15 the depth collector's websockets died silently and it kept reporting healthy for 3½
days. In a data collector that cost us data. In a live bot, dead sockets mean **unmonitored
positions**, which means a funding flip or an adverse move runs unattended into liquidation.

Rules, mirroring the fix already shipped in `depth_collectors.py`:

1. Every data source has a **staleness deadline**. Exceeding it is a failure, never a `continue`.
2. Failed heartbeats **propagate** — no blanket `suppress(Exception)` around a send.
3. Every reconnect logs venue, symbol count and **reason**.
4. **Stale data ⇒ the bot must not act.** If `funding_basis_snapshots` or `carry_book_l2` has not
   advanced within `MAX_DATA_STALENESS_MIN`, the bot refuses to open positions and (in live mode)
   trips the kill-switch. Silence must never look like health.

---

## 2. RISK RULES (explicit)

| # | rule | parameter | default | rationale |
|---|---|---|---|---|
| R1 | perp leverage | `LEVERAGE` | **2.0** (allowed 1.0–2.0, hard-capped at 2.0) | capital multiple 1.5× notional; higher leverage buys APR by buying liquidation risk |
| R2 | margin buffer | `LIQUIDATION_BUFFER_PCT` | survive a **+35%** adverse move before liquidation | at L=2× the naive liquidation is ~+45–50% on the short; 35% is the level at which we act, not the level at which we die |
| R3 | margin top-up | `MARGIN_TOPUP_MOVE_PCT` | **+20%** | the legs are on different venues, so spot gains do **not** automatically defend perp margin; top up before it is urgent |
| R4 | **funding-flip exit** | `FLIP_EXIT_EPOCHS`, `MIN_HOLD_APR` | 2 consecutive negative epochs, **or** trailing-7-epoch APR < **8%** | 8% is the floor at which the position stops beating BTC ballast plus its own exit cost |
| R5 | **depth-collapse exit** | `DEPTH_COLLAPSE_RATIO` | worst-hour depth < **50%** of entry-time worst-hour depth | 9 of 61 names collapse >2× at their worst hour, including WET (0.15) and PLAY (0.11) |
| R6 | per-name size cap | `MAX_RT_SLIP_BPS` | notional ≤ **worst-hour** capacity at 50 bps round trip | **worst-hour sizing, never median** — median overstates capacity ~30% typically and 7× on the worst names |
| R7 | per-venue cap | `MEXC_VENUE_CAP` | MEXC ≤ **40%** of deployed capital | counterparty/withdrawal risk. Costs ~€9.2k of deployment and ~2pp of APR versus 60% — a deliberate price |
| R8 | global kill-switch | `KILL_SWITCH_FILE` | file present ⇒ open nothing, flatten in live | manual stop that needs no deploy |
| R9 | auto kill-switch | `MAX_DRAWDOWN_PCT`, `MAX_DATA_STALENESS_MIN` | drawdown > **5%** of deployed capital, or data stale > **15 min** | an unmonitored bot must fail closed |
| R10 | position count | `MAX_POSITIONS` | 12 | operational limit; each name is a separate unwind under stress |

**On R6, emphatically:** every size in this bot is computed from the **thinnest hour of the day**,
not the median book. The whole point of the 84-hour depth study was that the median lies.

---

## 3. PAPER → LIVE

One flag: `CARRY_BOT_MODE=paper|live` (`bot/config.py`). Live additionally requires
`CARRY_BOT_ALLOW_LIVE=1` **and** trading credentials that do not currently exist — three
independent locks, so no single mistake can arm it.

**Identical in both modes** (this is the point of the design — the paper record must be evidence
about the live bot, not about a different bot):

- selection logic and every gate
- interval resolution and funding-epoch timing
- sizing, including worst-hour depth and both caps
- funding accrual maths
- all risk rules R1–R10 and the neutrality manager
- the `paper_carry_positions` schema and the runner log

**Swapped for live** (the entire surface that touches money):

| concern | paper | live |
|---|---|---|
| order placement | none — book-walk pricing | real maker orders, post-only, with retry/cancel |
| fills | VWAP walk of `carry_book_l2` | exchange fill reports |
| margin | modelled from leverage | exchange-reported margin and liquidation price |
| funding | `notional × funding_rate` at the epoch | exchange funding-payment records |
| failure of one leg | recorded as leg risk | **must** unwind the filled leg — the dangerous case |

**Go-live gate.** Not a date — a result. Multi-day paper accrual must land within a stated
tolerance of the 31–33% model, `realised_funding_usd` must track `modelled_funding_usd`, the
watchdog must have survived a real disconnect, and R4/R5 must have been observed firing. Then a
MEXC test withdrawal clears before any MEXC slice is funded.

---

## 4. WHAT THIS DESIGN DOES NOT DO

Stated so nobody mistakes silence for coverage:

- **No queue modelling.** Maker intent is assumed to fill at the touch. The ёрш study is the
  standing warning that queue reality is worse than book reality; a maker carry entry may
  simply not fill, and paper will not show that.
- **No borrow/margin cost on the spot leg.** Spot is assumed unlevered and fully funded.
- **No cross-venue transfer latency.** Rebalancing margin between venues is instant here and is
  not in life.
- **Funding is assumed to persist.** Gross APR is a trailing mean; high-APR alt funding is
  famously unstable. R4 is the mitigation, not a solution.
- **No slippage on the exit path beyond the modelled round trip**, and no gap risk.
