# Addendum to `RESEARCH_IMBALANCE_PREREG.md` — the sequencing test

**Written 2026-09-14, BEFORE the volume series was consulted for this test.** The original
pre-registration (commit `14a0633`) is unchanged and must stay unchanged; this file adds the one
definition that file did not contain, exactly as it instructed: *"If the analysis later needs a
definition you did not pre-register, say so explicitly rather than quietly adopting it."*

## Why a new definition is needed

Stage 2 found that volume changes lead the funding crossing. Funding is struck off the **perp
premium index**, which responds to the same order flow that produces volume — so the lead may be
**mechanically entailed** rather than informative. Deciding that requires a timestamp the original
prereg never defined: **when the premium itself began to move.**

## `t_premium` — fixed here, before looking at volume

The premium is `basis_bps = (perp_mark - spot)/spot * 1e4`, which is the quantity the funding rate
is computed from.

- **Baseline window:** `[t0 - 7d, t0 - 24h]`. It **ends before** the measurement window begins, so
  nothing inside the window under test can contaminate the threshold.
- **Baseline band:** `median +/- 3 * 1.4826 * MAD` of `basis_bps` over that window. MAD, not SD,
  because basis is heavy-tailed. Requires >= 200 observations or the episode is dropped.
- **`t_premium` = the FIRST timestamp in `[t0 - 24h, t0]` at which `basis_bps` leaves that band in
  the direction of the funding sign at t0** — upward for a positive-funding onset (perp above
  spot), downward for a negative one. Direction is required because that is the mechanical link
  being tested; an unsigned move is a different claim.
- If the premium never leaves the band inside the window, `t_premium` is undefined and the episode
  is reported separately, never silently dropped.
- **Exact timestamps, not row offsets.** This is a timing question and the row-offset approximation
  (72/288 rows at ~5.00 min cadence) has an error of minutes, which is the size of the effect.

## The lead window

`lead_minutes = t_premium - t_volume`, where `t_volume` is defined by the **same rule applied to
`spot_volume24_usd`** (same baseline window, same MAD band, same directional requirement — upward,
since the Stage-2 finding is that volume rises). A positive lead means volume moved first.

## New hypotheses declared, and the family widened

Three, added to the 20 already pre-registered:

| # | hypothesis |
|---|---|
| 21 | a volume-before-premium window exists with length > 0 |
| 22 | `svol_6` measured strictly BEFORE `t_premium` discriminates onsets (AUC != 0.5) |
| 23 | `svol_6` measured AFTER `t_premium` discriminates onsets (AUC != 0.5) |

**Family is now 23. Bonferroni alpha = 0.05/23 = 0.00217**, tightened from 0.0025. Reported
alongside unadjusted p-values with the count visible, as before.

## The decision rule, committed now

- **No window, or volume before `t_premium` does not discriminate** -> the lead is **ARITHMETIC**.
  Kill criterion #2 applies retroactively; the predictive line closes and converts to an
  explanation of carry's income.
- **A window exists AND volume before `t_premium` discriminates** -> the lead is **INFORMATION**,
  and Stage 3 stays open on the hypothesis the evidence actually supports, which after §2 of
  prompt-84 is churn, not accumulation.
