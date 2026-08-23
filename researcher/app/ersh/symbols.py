"""Candidate "ершистые" coins — thin but active perps.

Selected 2026-08-13 from the bulk perp tickers the carry collector already uses
(MEXC contract/ticker `amount24`, Gate futures/usdt/tickers `volume_24h_quote`).

Selection rule:
  • 24h quote volume in $50k–$3M — thin enough for a single dumb MM to matter,
    active enough to print tape.
  • Majors excluded (a real MM crowd, no ёрш pattern).
  • Tokenized-equity perps excluded — their tape follows US market hours, not a
    crypto MM. A few borderline names survive the filter (see EQUITY_SUSPECT).
  • Stratified into three volume tiers, 5 per tier per exchange, ranked by
    quoted spread inside each tier (wide spread = room for a maker edge).

Volumes below are the 24h quote volumes observed at selection time; they drift.
Prune this list if the tape row rate turns out too high.
"""
from __future__ import annotations

# symbol → approx 24h quote volume (USD) at selection time
MEXC_CANDIDATES: dict[str, int] = {
    # tier $50k–$300k (widest spreads)
    "FRONG_USDT":    181_587,
    "KET_USDT":      103_118,
    "AURASOL_USDT":  102_567,
    "WISHBONE_USDT": 110_994,
    "INDEX_USDT":    103_951,
    # tier $300k–$1M
    "ENJ_USDT":      400_829,
    "ANSEM_USDT":    405_402,
    "HOODRAT_USDT":  417_550,
    "ROBO_USDT":     588_187,
    "CATI_USDT":     345_939,
    # tier $1M–$3M
    "ONE_USDT":    2_266_066,
    "JIMOTHY_USDT": 1_348_730,
    "LAB_USDT":    1_188_539,
    "BLUAI_USDT":  1_049_394,
    "CASHCAT_USDT": 1_038_381,
}

GATE_CANDIDATES: dict[str, int] = {
    # tier $50k–$300k
    "WEN_USDT":      269_894,
    "SPCH_USDT":      54_050,
    "SKDD_USDT":      92_690,
    "CRDO_USDT":     108_291,
    "KIOXIA_USDT":   299_721,
    # tier $300k–$1M
    "MYX_USDT":      463_593,
    "KOMA_USDT":     379_183,
    "LA_USDT":       451_133,
    "CHILLGUY_USDT": 311_944,
    "FHE_USDT":      314_089,
    # tier $1M–$3M
    "BSP_USDT":    1_171_770,
    "ONE_USDT":    2_300_389,
    "AAOI_USDT":   1_786_490,
    "OKB_USDT":    2_208_062,
    "BMT_USDT":    2_954_738,
}

# Names that look like tokenized equities and may print only during US market
# hours. Kept for now — decide from the observed tape rate whether to drop them.
EQUITY_SUSPECT = {"CRDO_USDT", "KIOXIA_USDT", "AAOI_USDT"}

MEXC_SYMBOLS = list(MEXC_CANDIDATES)
GATE_SYMBOLS = list(GATE_CANDIDATES)


# ── PROMPT-56 §5: carry-survivor tape set, added 2026-08-19 ──────────────────
# The 45 corrected-carry survivors. Collected by a SEPARATE unit
# (mexc-carry-tape, ERSH_SYMBOL_SET=carry) so the ёрш series above is never
# interrupted. TIME-BOXED: the authenticity screen needs ~1-3 days of prints,
# not a permanent stream. STOP DATE: 2026-08-22.
# reversal_gate FAIL names are present for MEASUREMENT ONLY.
_ALREADY_IN_ERSH_MEXC = {"BLUAI_USDT", "LAB_USDT"}      # collected by mexc-ersh-tape
_ALREADY_IN_ERSH_GATE = {"ONE_USDT"}                    # collected by mexc-ersh-tape

_CARRY_TAPE_MEXC_ALL: list[str] = [
    "BTW_USDT",           # net 135.8%  reversal_gate=PASS
    "BLUAI_USDT",         # net 71.0%  reversal_gate=PASS
    "LYN_USDT",           # net 65.2%  reversal_gate=PASS
    "ZEST_USDT",          # net 59.0%  reversal_gate=PASS
    "PLAY_USDT",          # net 56.4%  reversal_gate=PASS
    "H_USDT",             # net 56.0%  reversal_gate=PASS
    "HANA_USDT",          # net 55.8%  reversal_gate=PASS
    "POWER_USDT",         # net 51.7%  reversal_gate=PASS
    "BULLA_USDT",         # net 50.8%  reversal_gate=PASS
    "US_USDT",            # net 49.5%  reversal_gate=PASS
    "APR_USDT",           # net 45.9%  reversal_gate=PASS
    "PRL_USDT",           # net 45.5%  reversal_gate=PASS
    "LAB_USDT",           # net 45.0%  reversal_gate=PASS
    "BTR_USDT",           # net 43.0%  reversal_gate=PASS
    "TRUTH_USDT",         # net 42.5%  reversal_gate=PASS
    "CLANKER_USDT",       # net 40.1%  reversal_gate=PASS
    "XPIN_USDT",          # net 39.5%  reversal_gate=PASS
    "RIVER_USDT",         # net 36.8%  reversal_gate=PASS
    "TAC_USDT",           # net 36.8%  reversal_gate=PASS
    "O_USDT",             # net 36.2%  reversal_gate=PASS
    "TA_USDT",            # net 35.2%  reversal_gate=PASS
    "ACU_USDT",           # net 32.6%  reversal_gate=PASS
    "EVAA_USDT",          # net 32.4%  reversal_gate=PASS
    "M_USDT",             # net 32.3%  reversal_gate=PASS
    "VELVET_USDT",        # net 30.2%  reversal_gate=PASS
    "GUA_USDT",           # net 63.3%  reversal_gate=FAIL
    "IDOL_USDT",          # net 39.5%  reversal_gate=FAIL
    "AKE_USDT",           # net 31.5%  reversal_gate=FAIL
]
_CARRY_TAPE_GATE_ALL: list[str] = [
    "AI_USDT",            # net 71.5%  reversal_gate=PASS
    "WET_USDT",           # net 67.3%  reversal_gate=PASS
    "HANA_USDT",          # net 62.6%  reversal_gate=PASS
    "IDOL_USDT",          # net 59.4%  reversal_gate=PASS
    "TRUST_USDT",         # net 56.7%  reversal_gate=PASS
    "PTB_USDT",           # net 49.5%  reversal_gate=PASS
    "AIO_USDT",           # net 45.1%  reversal_gate=PASS
    "BTR_USDT",           # net 42.2%  reversal_gate=PASS
    "IN_USDT",            # net 36.8%  reversal_gate=PASS
    "TAKE_USDT",          # net 34.2%  reversal_gate=PASS
    "INX_USDT",           # net 33.9%  reversal_gate=PASS
    "STBL_USDT",          # net 31.6%  reversal_gate=PASS
    "ELSA_USDT",          # net 30.8%  reversal_gate=PASS
    "龙虾_USDT",            # net 127.5%  reversal_gate=FAIL
    "TUT_USDT",           # net 55.8%  reversal_gate=FAIL
    "POWER_USDT",         # net 55.1%  reversal_gate=FAIL
    "ONE_USDT",           # net 41.9%  reversal_gate=FAIL
]

# Double-collection into tape_prints would duplicate every print for the
# overlapping names and silently corrupt their observed volume. Excluded here;
# their tape already exists from the ёрш unit.
CARRY_TAPE_MEXC: list[str] = [s for s in _CARRY_TAPE_MEXC_ALL if s not in _ALREADY_IN_ERSH_MEXC]
CARRY_TAPE_GATE: list[str] = [s for s in _CARRY_TAPE_GATE_ALL if s not in _ALREADY_IN_ERSH_GATE]
