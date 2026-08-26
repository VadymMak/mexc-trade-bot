# Validating the tape-authenticity screen before it is pointed at carry survivors

**Date:** 2026-08-19 · **Analyst:** Claude (session on `trading-server`) · §6 of prompt-56
**Status:** READ-ONLY. No writes, no schema change, no service touched by this analysis.

**Sample regime: collected 2026-07-27 → 2026-08-19 (23 days; tape from 2026-08-13, 6 days)
during a bear market — BTC topped $126,199 in Oct 2025 and traded near $60,000 in mid-2026, a
>50% drawdown. Thin-name liquidity is depressed market-wide. One regime, one sample.**

---

## Why this exists

The screen was built, scored HOODRAT as **CLEAN (84.0)**, and per-day scoring plus a veto rule
were added *after* seeing that failure. It is therefore partly fitted to the one example that
motivated it. It is about to be pointed at 42 carry survivors that currently have no
authenticity evidence, so it must be validated first.

**Summary of what survived validation:**

| test | result |
|---|---|
| HOODRAT hold-out | **Fails on worst-day scoring, not on the veto rule.** The veto rule is not load-bearing; worst-day scoring is — and that IS the HOODRAT-informed choice. |
| Temporal hold-out | **5 of 28 symbols flip** between halves, all in the direction of deterioration. |
| ±25% sensitivity | **8 of 29 symbols are threshold-sensitive** and must be reported NOT CLASSIFIED — including **gate/LA**, the ёрш flagship. |
| Uniformity index | **Survives out-of-sample with zero false positives in both halves.** Promote it. |
| SPCH / WEN | **Can never be classified by waiting.** The rule is structurally unreachable for them. |

---

## §6.1 — HOODRAT hold-out: is the disqualification an artifact of HOODRAT-informed tuning?

The seven component anchors are *a priori* — they come from market structure (a taker must
cross the spread; organic order flow is sign-persistent; organic flow is bursty), not from
looking at HOODRAT. What **was** chosen after seeing HOODRAT is (a) **per-day worst-day
scoring** and (b) the **veto rule**. So the honest test is whether HOODRAT still fails when
those two are varied or removed.

### Sweeping the veto floor, including removing the veto rule entirely

| veto floor | HOODRAT verdict | worst-day score | vetoes fired | population |
|---|---|---|---|---|
| **0.0 (no veto rule)** | **DISQUALIFIED** | 30.7 | 0 | 13 CLEAN / 8 SUSPECT / 8 DISQ |
| 5.0 | DISQUALIFIED | 30.7 | 3 | identical |
| 10.0 (default) | DISQUALIFIED | 30.7 | 3 | identical |
| 15.0 – 25.0 | DISQUALIFIED | 30.7 | 3 | identical |

**The veto rule is not load-bearing.** HOODRAT scores 30.7 on its worst day, well below the 45
DISQUALIFIED boundary, so it fails on score alone. Sweeping the floor from 0 to 25 changes not
one verdict anywhere in the population.

### But worst-day scoring *is* load-bearing — and that is the fitted choice

| scoring rule | HOODRAT score | verdict |
|---|---|---|
| mean of daily scores (what the first screen effectively did) | **57.4** | **SUSPECT** |
| **worst day** (the post-HOODRAT choice) | **30.7** | **DISQUALIFIED** |

**Stated plainly, as the brief requires: the screen only fails HOODRAT because of a design
decision made after seeing HOODRAT fail.** Under averaging it is SUSPECT, not CLEAN — so the
original 84.0 is not reproduced, and the difference is partly that the tape has grown since —
but it is still not DISQUALIFIED without worst-day scoring.

**Is worst-day scoring nonetheless justifiable independently?** I think yes, and the argument
does not reference HOODRAT: a strategy holding inventory must survive the bad days, so the
binding observation is the worst one, not the average one. But that is a *reasoned* defence of
a choice made after the fact, not an out-of-sample validation of it. **The distinction should
be held onto, not smoothed over.**

---

## §6.2 — Temporal hold-out (first half → second half, split 2026-08-16)

| symbol | 1st half | 2nd half | |
|---|---|---|---|
| gate/BSP_USDT | CLEAN | SUSPECT | **flip** |
| mexc/BLUAI_USDT | CLEAN | SUSPECT | **flip** |
| mexc/LAB_USDT | CLEAN | SUSPECT | **flip** |
| mexc/CATI_USDT | SUSPECT | DISQUALIFIED | **flip** |
| mexc/HOODRAT_USDT | SUSPECT | DISQUALIFIED | **flip** |
| *(23 others)* | | | stable |

**5 of 28 symbols scored in both halves flip. Every flip is toward a worse verdict.**

That pattern matters. Random instability would flip in both directions; monotone deterioration
is consistent with genuine regime change in the underlying markets — which is exactly what the
per-day series showed for HOODRAT (spread 57 → 376 bps) and now shows for CATI. **So the flips
are more likely a property of the market than of the screen.**

But the operational consequence is the same either way: **a verdict has a shelf life of days,
not weeks.** Any use of this screen on the carry survivors must be re-run, not cached.

---

## §6.3 — Sensitivity: each veto-eligible anchor ±25%

| component | −25% | +25% |
|---|---|---|
| book_cross | 1 change (BSP) | 2 changes (**LA**, BLUAI) |
| sign_persist | 2 changes (AAOI, BLUAI) | 3 changes (BSP, CATI, LAB) |
| size_conc | 2 changes (mexc/ONE, BLUAI) | 1 change (CRDO) |
| burstiness | **0** | **0** |
| size_tail | **0** | **0** |

**8 of 29 symbols flip verdict under at least one ±25% perturbation. Per the brief they are NOT
CLASSIFIED:**

`gate/AAOI` (CLEAN) · `gate/BSP` (SUSPECT) · `gate/CRDO` (SUSPECT) · **`gate/LA` (CLEAN)** ·
`gate/ONE` (CLEAN) · `mexc/BLUAI` (SUSPECT) · `mexc/CATI` (DISQUALIFIED) · `mexc/LAB` (SUSPECT)

### The finding that matters most

**`gate/LA_USDT` — the ёрш flagship and a carry survivor — is threshold-sensitive.** Under
`book_cross` +25% it goes CLEAN (74.8) → **SUSPECT (69.6)**. It sits 4.8 points above the
CLEAN/SUSPECT boundary. Its CLEAN verdict is real but **not robust**, and it should not be
treated as settled evidence of authenticity.

`burstiness` and `size_tail` are perfectly insensitive over ±25% — they separate the population
so cleanly that no plausible threshold change touches a verdict. `book_cross` and `sign_persist`
carry the marginal cases.

Also note **`mexc/CATI` and `mexc/LAB` appear in both §6.2 and §6.3** — they are unstable in
time *and* in threshold. Those two are the least trustworthy classifications in the set.

---

## §6.4 — The uniformity index, validated out-of-sample

`uniformity_index = top1_size_share × distinct_sizes` (≈1 for a uniform grid, ≫1 for organic).
It was deliberately withheld from the score, so a temporal hold-out is a genuine out-of-sample
test.

| | correct synthetic | **false alarms** | correct organic | missed synthetic | ambiguous (10–35) |
|---|---|---|---|---|---|
| 1st half | 7 | **0** | 15 | 1 | 5 |
| 2nd half | 7 | **0** | 13 | 0 | 8 |

**Zero false positives in both halves.** No CLEAN name ever scored below 10.

Six names sit stably below 10 in *both* halves — AURASOL (1.8 / 1.6), ENJ (2.7 / 2.4), INDEX
(2.9 / 2.7), FRONG (3.1 / 3.4), WISHBONE (3.2 / 2.0), KET (4.5 / 4.3) — exactly the
manufactured set, and stable across the split (ratios 0.62–1.09).

The single "miss" is **HOODRAT in the first half (UI 143.2 → 4.6)**, which is not a failure of
the index: it is the index *correctly tracking* HOODRAT's transition from organic-sized flow to
minimum-size dust. It is arguably the cleanest single measurement of that transition anywhere in
this work.

**Verdict: promote it.** It survives out-of-sample with perfect precision. Two caveats:

1. It is **high-precision, moderate-recall**. The ambiguous 10–35 band grew from 5 to 8 names
   between halves, so it classifies fewer names in a quieter window. Use it as an additional
   **veto-eligible component**, not as a sole classifier.
2. `mexc/CATI` straddles the boundary (9.0 → 13.0), which is a third independent indication that
   CATI is the least secure verdict in the set.

**Recommended change:** add `uniformity_index` as a veto-eligible component with anchors
(0 at UI ≤ 10, 100 at UI ≥ 35), weight 10, taken from `benford` (5) and `price_disp` (5) —
the two components §6.3 and the original report both show do not discriminate. This *replaces
weaker components* exactly as the brief allows, and it is the only component here with an
out-of-sample validation behind it.

---

## §6.5 — The known weak spots

### mexc/CATI — the weakest disqualification, revisited

| day | n | crossed | alternation | CV | top1 | Hill α |
|---|---|---|---|---|---|---|
| 08-13 | 3,933 | 0.240 | 0.467 | 0.52 | 0.031 | 1.47 |
| 08-14 | 8,827 | 0.212 | 0.477 | 0.46 | 0.030 | 1.16 |
| 08-15 | 8,444 | 0.190 | 0.503 | 1.13 | 0.033 | 0.93 |
| 08-16 | 8,158 | 0.198 | 0.501 | 0.35 | 0.034 | 1.61 |
| 08-17 | 8,515 | **0.644** | 0.529 | 0.41 | 0.034 | 0.62 |
| 08-18 | 8,381 | **0.792** | 0.635 | 0.38 | 0.034 | 0.70 |
| 08-19 | 4,784 | **0.816** | 0.627 | 0.40 | 0.034 | 0.65 |

The original note said CATI was disqualified without `book_cross` firing. That is confirmed and
now explained: **CATI's crossing rate transitions mid-window**, 0.19 → 0.82. Its disqualification
rests on `sign_persist` (alternation 0.467–0.635, i.e. a coin flip or worse throughout) and
`burstiness` (CV 0.35–0.52, more regular than Poisson, throughout). Both are stable across all
seven days, so the verdict is better founded than "book_cross didn't fire" implied.

**But it flips under sign_persist +25%, flips between temporal halves, and straddles the
uniformity boundary.** Three independent instabilities. **CATI should be reported as NOT
CLASSIFIED**, not as DISQUALIFIED.

### gate/SPCH and gate/WEN — how long until a verdict?

| symbol | total prints | prints/day | **best single day** | rule needs |
|---|---|---|---|---|
| gate/SPCH_USDT | 1,004 | 167 | **404** | 500 in one calendar day |
| gate/WEN_USDT | 2,371 | 395 | **476** | 500 in one calendar day |

**Neither will ever be classified by waiting.** Both peak *below* the 500-print threshold on
their best day, and the threshold is per calendar day. More collection time adds more days that
each individually fail. This is a structural defect in the rule, not a data shortage.

At observed rates, to accumulate 500 prints in a **pooled rolling window**:
SPCH **3.0 days**, WEN **1.3 days**. For a stable CV / Hill estimate (n ≈ 1,000):
SPCH **6.0 days**, WEN **2.6 days**.

**Recommended change:** score on a **rolling 500-print window** rather than a calendar day. This
keeps the worst-window logic (which §6.1 shows is load-bearing) while making thin honest names
classifiable. It would bring SPCH and WEN into scope within days rather than never.

---

## What this means for pointing the screen at the carry survivors

The screen may be used, with four amendments applied first:

1. **Report the 8 threshold-sensitive names as NOT CLASSIFIED**, including gate/LA and CATI.
   A verdict that moves under a ±25% threshold nudge is not a verdict.
2. **Swap `benford` (5) + `price_disp` (5) for `uniformity_index` (10).** It is the only
   component with out-of-sample validation and zero false positives.
3. **Score on a rolling 500-print window, not a calendar day**, so thin honest names are not
   permanently INCONCLUSIVE.
4. **Re-run rather than cache.** Verdicts have a shelf life of days (§6.2).

And one thing to keep saying out loud: **worst-day scoring is a post-hoc choice that is doing
the work.** It is defensible on its own terms, but it has not been validated out-of-sample and
the HOODRAT result is not independent evidence for it.

---

## What I could not determine, and why

1. **Whether worst-day scoring generalises.** It is the load-bearing choice and it was made
   after seeing the failure it fixes. Validating it needs a *second* known-bad name that the
   averaged screen passes — and the only labelled bad name in the project is HOODRAT.
2. **Whether the 5 temporal flips are market change or screen instability.** They all move one
   way, which favours market change, but with 6 days of tape and one split point I cannot
   separate the two. A longer series with multiple split points would.
3. **A recall figure for the uniformity index.** Precision is 100% out-of-sample, but "missed
   synthetic" can only be counted against the screen's own labels, which are not ground truth.
   The index and the screen are not independent, so the 0 false alarms is the trustworthy half
   of that table and the recall half is not.
4. **Whether ±25% is the right sensitivity band.** It is the brief's number, not a derived one.
   A different band would produce a different NOT-CLASSIFIED list; 8 of 29 is specific to ±25%.
5. **Anything about the 42 carry survivors.** Their tape collection started 2026-08-19T13:30Z
   and needs 1–3 days. This validation deliberately runs first so the screen is settled before
   the data lands.
6. **Whether the screen works on a different venue mix.** All 29 validated symbols are the ёрш
   selection (thin MEXC/Gate perps, $50k–$3M volume). The carry survivors overlap that profile
   but were selected on funding, not volume.
