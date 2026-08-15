"""Carry starter basket — the only names we collect depth for.

Picked 2026-08-15 by the Phase 1 carry screen (CARRY_CANDIDATES.md) out of 129
coin-venues that cleared every stability gate, then re-ranked in Phase 2 once the
REAL funding interval and net-on-CAPITAL were applied.

Phase 2 corrected economics (net on deployed capital, L=2x, H=7d, maker entry):

  venue symbol        real_iv  gross_corr  gross_old   NET@L2,H7   perp/spot spr
  mexc  PLAY_USDT        4h       58.99      27.10       37.93      2.87 / 18.97
  gate  WET_USDT         8h       59.64      59.64       36.98      8.38 / 15.20
  gate  HANA_USDT        8h       57.00      57.00       35.22      6.90 / 10.68
  gate  IDOL_USDT        4h       53.12      25.64       32.63      8.92 /  4.76
  gate  BTR_USDT         8h       32.09      32.09       18.62     10.18 / 11.55
  mexc  BTC_USDT         8h        6.36       6.36        2.85      0.02 /  0.00

PLAY and IDOL settle funding every 4h, not the 8h the carry collector hardcodes,
so their APR was understated 2.07-2.18x in Phase 1. BTC is ballast/control.

WHY THIS TABLE EXISTS: funding_basis_snapshots has perp_depth5_usd and
spot_depth5_usd 100% NULL, so the entire Phase 1 screen is SIZE-BLIND. We do not
know whether these names absorb EUR 1k, let alone EUR 30k. carry_book_l2 exists
to answer exactly that, and nothing else.
"""
from __future__ import annotations

# (exchange, symbol) — perp AND spot depth are collected for each
CARRY_BASKET: list[tuple[str, str]] = [
    ("gate", "HANA_USDT"),
    ("gate", "WET_USDT"),
    ("gate", "IDOL_USDT"),
    ("gate", "BTR_USDT"),
    ("mexc", "PLAY_USDT"),
    ("mexc", "BTC_USDT"),
]

GATE_SYMBOLS: list[str] = [s for ex, s in CARRY_BASKET if ex == "gate"]
MEXC_SYMBOLS: list[str] = [s for ex, s in CARRY_BASKET if ex == "mexc"]

# top-N book levels stored per side
LEVELS = 10


def spot_symbol(exchange: str, symbol: str) -> str:
    """Venue-native spot ticker for a perp symbol."""
    if exchange == "mexc":
        return symbol.replace("_", "")      # PLAY_USDT -> PLAYUSDT
    return symbol                            # gate spot uses HANA_USDT
