# Ёрш — queue-aware maker-fill verdict (read-only, 2026-08-15)

**Call: PARK IT.**

Ёрш is a **queue game, not a latency game — and a non-colocated bot loses it.** The edge is
real: at the front of the queue it is worth **+12 to +14 bps per quoting cycle** on LA and
ONE. But it belongs entirely to whoever holds queue priority. Joining the back of the queue —
the only thing we can actually do — turns every candidate negative:
**−0.7 to −16.7 bps per cycle.**

Latency is almost irrelevant: **50 ms and 1000 ms give the same answer** (LA −0.68 vs −0.85).
Buying a faster link fixes nothing. The binding constraint is queue position, which requires
camping a price level continuously and never losing it — i.e. a colocated always-on quoting
engine competing with people who do only that.

One selection error also surfaced: **`mexc ONE_USDT` is not locked-1-tick** and never was
(details below), so it should not have been in the candidate set.

---

## Step 0 — Coverage (which candidates survive)

~21.7 h of L2 for all five, clean structure (exactly 10 rows per snapshot, 5 bid + 5 ask).

| candidate | L2 snaps | hours | tape prints | prints/min | L2 snaps/min | survives? |
|---|---|---|---|---|---|---|
| gate `ONE_USDT` | 66,923 | 21.72 | 81,329 | **62.4** | 51.3 | ✅ |
| gate `BMT_USDT` | 64,952 | 21.73 | 43,717 | 33.5 | 49.8 | ✅ |
| gate `LA_USDT` | 39,357 | 21.73 | 19,353 | 14.8 | 30.2 | ✅ |
| mexc `ONE_USDT` | 53,384 | 21.73 | 18,562 | 14.2 | 41.0 | ⚠️ **disqualified — see tick grid** |
| gate `MYX_USDT` | 23,688 | 21.72 | 3,432 | **2.6** | 18.2 | ⚠️ **too thin** |

**`gate MYX_USDT` excluded from the verdict.** At 2.6 prints/min the queue barely drains:
**90.8 % of quoting cycles produced no fill at all** and both sides filled in **0.1 %**. There
is no tradeable flow here; any number computed on it is noise, and I am not issuing a verdict
on it.

### Tick grid — measured, not assumed (this corrects the detector)

Tick inferred from the minimum gap between adjacent L2 price levels, cross-checked against
price-level granularity.

| candidate | tick | avg price | 1 tick (bps) | median spread | **= ticks** | detector said |
|---|---|---|---|---|---|---|
| gate `LA_USDT` | 1e-4 | 0.05046 | 19.82 | 19.94 bps | **1** ✅ | 1 ✅ |
| gate `ONE_USDT` | 1e-6 | 0.00069764 | 14.33 | 14.23 bps | **1** ✅ | 1 ✅ |
| gate `MYX_USDT` | 1e-4 | 0.07413 | 13.49 | 13.54 bps | **1** | 2 |
| gate `BMT_USDT` | 1e-5 | 0.01648 | 6.07 | 12.48 bps | **2** ✅ | 2 ✅ |
| mexc `ONE_USDT` | **1e-7** | 0.00071925 | **1.39** | 32.10 bps | **~23** ❌ | 1 ❌ |

**`mexc ONE_USDT` sits on a 1e-7 grid, 100× finer than Gate's 1e-6.** Verified directly:
**29.9 % of its L2 prices require finer than 1e-6 granularity**, and it shows **1060 distinct
prices vs Gate's 225**. Its ~32 bps spread is therefore **~23 ticks wide, not 1**.

That breaks the entire premise the candidate set was built on — "nobody can queue ahead of you
by improving the price, because there is no price in between." On mexc ONE anyone can step in
front for **1.39 bps**. It is the FRONG failure mode the detector explicitly warned about, and
it was mis-classified. Its otherwise-attractive front-of-queue number (+13.5) is meaningless,
because the front of that queue is not defensible.

---

## Step 1/2 — Model

Two-sided quoting, which is what ёрш actually is: post a bid at b1 **and** an ask at a1
simultaneously, **do not chase**. You capture the spread when both fill (price oscillated);
you get adversely selected when only one fills and price keeps going (price trended). This is
why the detector ranked on reversal rate.

> A first version chased the touch with the second leg after the first filled. That is
> momentum-chasing, not spread capture, and lost by construction (−3 to −28 bps gross
> everywhere). Discarded and rebuilt.

- **Units:** contracts throughout. `ersh_book_l2.size` and `tape_prints.size` are both
  contracts on both venues, so no multiplier is involved and there is **no unit risk**.
- **Queue:** `queue_ahead = qfrac ×` resting size at our level from the last L2 snapshot
  observable ≥ latency ago. `qfrac=1.0` = back of queue (us), `0.0` = front (colocated ideal).
- **Fill:** our BUY at b1 fills only after SELL-aggressor prints at price ≤ b1 consume the
  queue ahead; our SELL at a1 after BUY-aggressor prints at price ≥ a1. Binary.
- **Cycle:** 60 s. Unfilled side cancelled; inventory marked to **book mid**, never trade price.
- **Fees:** MEXC maker **0.0 bp** (confirmed, `backend/app/market_data/mexc_http.py:230`).
  Gate run under **two scenarios** because the −1.0 bp rebate asserted in `l2_symbols.py` is
  **unverified**: `−1.0 bp (rebate)` and `+2.0 bp (posted retail)`.

## Results — back of queue (qfrac=1.0, 250 ms), Gate rebate assumed (generous)

| candidate | regime | cycles | both% | spread_cap | advsel | **NET/cycle** |
|---|---|---|---|---|---|---|
| gate `LA_USDT` | locked-1tk | 1301 | 1.9 % | +0.37 | −1.45 | **−0.71** |
| mexc `ONE_USDT` | widened | 1379 | 11.5 % | +4.08 | −6.29 | **−2.21** |
| gate `BMT_USDT` | locked-1tk | 365 | 12.6 % | +0.77 | −9.14 | **−7.64** |
| gate `ONE_USDT` | widened | 161 | 13.7 % | +3.66 | −13.67 | **−9.33** |
| gate `ONE_USDT` | locked-1tk | 1239 | 12.7 % | +1.77 | −12.79 | **−10.24** |
| gate `BMT_USDT` | widened | 1051 | 14.3 % | +2.01 | −19.43 | **−16.74** |

At posted retail Gate fees (+2.0 bp) every Gate row loses a further ~0.7–1.6 bps/cycle
(LA −1.80, gate ONE −12.57, BMT −18.79).

**The mechanism is stark: you get one-sided (adversely selected) 3–4× more often than you
capture the spread.** Cycle outcomes: both sides fill 0.1–14.3 % of the time, one side fills
9–51 %. Adverse-selection markout on those one-sided fills at 60 s:

| candidate | mo@1s | mo@5s | mo@30s | mo@60s |
|---|---|---|---|---|
| gate `BMT_USDT` | −18.78 | −21.79 | −32.15 | **−41.76** |
| gate `ONE_USDT` | −17.35 | −18.81 | −23.57 | **−24.98** |
| mexc `ONE_USDT` | −7.07 | −9.01 | −13.01 | **−13.90** |
| gate `MYX_USDT` | −5.25 | −4.52 | −3.15 | −4.51 |
| gate `LA_USDT` | −2.49 | −3.02 | −4.33 | **−4.40** |

Markout worsens monotonically with horizon on every name — the fills we get are the ones that
keep going against us. `BMT` is toxic: **−41.8 bps** by 60 s.

## Step 3 — The decisive test: queue position vs latency

NET bps/cycle, Gate rebate assumed (most generous fees):

| candidate | qfrac=1.0 (us) | qfrac=0.5 | qfrac=0.0 (colocated ideal) |
|---|---|---|---|
| gate `LA_USDT` | −0.70 | **+0.23** | **+12.34** |
| mexc `ONE_USDT` | −2.17 | −0.30 | +13.64 |
| gate `ONE_USDT` | −10.14 | −9.49 | +2.57 |
| gate `MYX_USDT` | −0.26 | −0.37 | +0.96 |
| gate `BMT_USDT` | −14.39 | −13.61 | **−9.48** |

Latency sweep at qfrac=1.0 — **essentially flat**:

| candidate | 50 ms | 250 ms | 1000 ms |
|---|---|---|---|
| gate `LA_USDT` | −0.68 | −0.70 | −0.85 |
| gate `ONE_USDT` | −10.04 | −10.14 | −10.13 |
| gate `BMT_USDT` | −15.17 | −14.39 | −15.14 |
| mexc `ONE_USDT` | −2.17 | −2.17 | −2.69 |

**This is the whole answer.** A 20× latency change moves the result by <1 bp. Moving from the
back of the queue to the front moves it by **13 bps** and flips the sign. Ёрш is not a race we
can win by being faster; it is a queue we cannot get to the front of. And `BMT` is negative
**even at the colocated ideal** — it is unprofitable for anyone quoting it passively.

### Per candidate

- **`gate LA_USDT` — least bad, still negative.** −0.70/cycle at the back, ~breakeven (+0.23)
  mid-queue, +12.34 at the front. Lowest adverse selection in the set (−4.4 bps @60s), matching
  its 97.8 % reversal rate. The only name worth ever revisiting — but "breakeven if we were
  mid-queue" is not a business, and at retail Gate fees it is −1.80.
- **`gate ONE_USDT` — no.** −10.2/cycle. Deep adverse selection (−25 bps @60s) and only +2.57
  even at the front.
- **`gate BMT_USDT` — no, structurally.** −14 to −17/cycle and **−9.5 even at the front**.
- **`mexc ONE_USDT` — disqualified.** Not locked-1-tick (~23 ticks); premise invalid.
- **`gate MYX_USDT` — no verdict.** Too thin to model (2.6 prints/min, 90.8 % dead cycles).

**Is any of the five net-positive for a non-colocated bot, in any regime? No. Not one.**

---

## Every optimistic simplification (things that could still make this too rosy)

1. **Our own order size is treated as 0 contracts.** A real order must also clear, strictly
   worse. All numbers assume an infinitesimal clip — and at real size the fills get worse still.
2. **Binary fill, no partials.** We count a fill the instant the queue ahead is exhausted.
3. **Gate maker rebate of −1.0 bp is assumed but UNVERIFIED.** The headline table uses it.
   At posted retail (+2.0 bp) every Gate result is ~0.7–1.6 bps/cycle worse. Nothing here
   turns positive under either scenario, but the true fee should be confirmed.
4. **Inventory marked to mid at 60 s.** Actually getting out means crossing the spread —
   another ~half-spread of real cost that is *not* charged anywhere in these numbers.
5. **No order rejection, no post-only reprice, no cancel-race cost, no exchange downtime.**
6. **No self-impact.** Our quote would change other participants' behaviour.
7. **21.7 hours, one window, one regime.** A single weekend-ish sample. `mexc ONE` widened ~6×
   during it, which is exactly why everything is reported conditioned on spread regime.

### And the one big PESSIMISM, stated honestly

**My queue only advances via trades, never via cancellations.** In real books most queue
turnover is cancels, so a bot camping a level would advance faster than modelled, and true
performance sits somewhere between `qfrac=1.0` and `qfrac=0.5`. That is precisely why the
sweep is the headline rather than a single number.

It does not rescue the conclusion. Even at `qfrac=0.5` — a generous proxy for "half the queue
ahead evaporated without trading" — only **LA (+0.23)** is above water, by a quarter of a basis
point, on assumed rebate fees, before order size, before exit costs. Everything else stays
firmly negative.

---

## One-line call

**Ёрш = park it.** Not worth real capital, and not worth more data collection either — the
existing 21.7 h already answers the question decisively, and the answer is structural rather
than sample-limited: the edge is real but it is priced into queue priority we cannot obtain.
If ёрш is ever revisited, it is **`gate LA_USDT` only**, and only behind hard evidence that we
can hold durable queue priority — which is the thing a non-colocated bot definitionally cannot do.

---

## Data integrity

Read-only session. No writes, no schema changes, no service changes, no push.

| table | rows now | status |
|---|---|---|
| `spread_observations` | 471,481 | **frozen, unchanged** (window still 2026-07-06 17:34 → 2026-07-27 13:06) |
| `funding_basis_snapshots` | 6,518,912 | intact (carry collector live) |
| `book_ticker` | 598,253 | intact (ёрш tape collector live) |
| `tape_prints` | 1,022,164 | intact (ёрш tape collector live) |
| `ersh_book_l2` | 2,498,970 | intact (ёрш L2 collector live) |

Row growth in the live tables is the running collectors; `spread_observations` is byte-stable.
