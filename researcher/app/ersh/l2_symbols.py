"""Locked-1-tick ёрш candidates — the only names worth L2 queue modelling.

Picked 2026-08-14 by the tape detector over the first 26.4 h of tape_prints /
book_ticker. The whole candidate set was scored on executable maker terms:

    edge_bps = median_spread/2 − mid-markout@60s − real maker fee × 2

Two findings drove this shortlist:

  • Wide *bps* spreads on the thin MEXC names are an artefact of an EMPTY book,
    not of a lazy market maker: FRONG quotes 3.7 % wide, which is 178 ticks —
    anyone can improve it by one tick (2.1 bps), so that width is never
    collectable. Those names also trade ≈ $70/min, which is untradeable size.

  • The names below quote a spread of ONE tick (BMT/MYX: two). Nobody can queue
    ahead of you by improving the price, because there is no price in between.
    The spread is therefore genuinely collectable and the entire P&L reduces to
    QUEUE POSITION at the touch — which book_ticker cannot answer, because it
    stores best_bid/best_ask but no sizes. Hence ersh_book_l2.

Observed at selection (26.4 h window):
  symbol          sprTk  spread/2  markout60  maker fee  edge   $/min   rev%
  gate LA_USDT      1      10.6       0.9       -1.0    +11.7    463    97.8
  gate MYX_USDT     2      12.0       3.5       -1.0    +10.5    372    59.6
  gate ONE_USDT     1       7.9       1.3       -1.0     +8.5    880    85.4
  mexc ONE_USDT     1       7.7       2.8        0.0     +4.9    760    79.0
  gate BMT_USDT     2       6.1       4.7       -1.0     +3.4    918    49.1

rev% = share of consecutive price moves that reverse sign. LA at 97.8 % is a
price that does almost nothing but bounce between bid and ask — the purest ёрш
structure in the set — and it drifted 0.0 % over the window.

CAVEAT carried forward: `edge` above assumes a fill. It is an upper bound. The
queue-aware fill simulator this table feeds exists precisely to find out how
much of it survives once you only fill when the tape prints through your level
AND you were at the front of the queue.
"""
from __future__ import annotations

MEXC_L2_SYMBOLS: list[str] = [
    "ONE_USDT",
]

GATE_L2_SYMBOLS: list[str] = [
    "LA_USDT",
    "ONE_USDT",
    "MYX_USDT",
    "BMT_USDT",
]
