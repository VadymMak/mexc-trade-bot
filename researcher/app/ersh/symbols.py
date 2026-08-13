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
