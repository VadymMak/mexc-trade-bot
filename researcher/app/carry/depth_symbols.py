"""Carry DEPTH-STUDY universe — the names we measure real capacity for.

RUN 2 (2026-08-19): the FULL 129 Phase-1 qualifying set, up from 61.

WHY ALL 129 NOW: run 1 answered the breadth question for the top 61 and the
answer was that capacity, not funding, binds — EUR 20.8k deployable at 17.1%
net APR under a MEXC<=40% counterparty cap, with GATE the scarce venue by 15x
(EUR 12.5k of Gate capacity against EUR 186k of MEXC). The 68 names left out
are 41 Gate names, which is exactly the scarce resource. Their pre-capacity APR
is low (max 4.6%, median 4.0%), so they will not be a yield discovery — but
whether they add usable GATE room is the open question that decides if EUR 30k
is reachable at a 40% MEXC cap. Measuring them is the only way to close it.

DISK BUDGET (measured from run 1, not guessed):
  137.4 bytes/row incl. index.  At a 60s throttle a perp stream produced exactly
  1.00 snapshot/min (144k rows/day/name) and a spot stream 0.80 of that (115k).
  129 names x 2 markets at 120s  ->  ~16.7M rows/day  ~2.30 GB/day.
  7 days ~ 16.1 GB against 47 GB free (carry_book_l2 already holds 3.3 GB from
  run 1, which is FROZEN and must not be deleted — the spot half of it is the
  Step-1 worst-hour evidence).

WHY 120s AND NOT FEWER NAMES: worst-hour capacity needs hourly COVERAGE, not
intra-minute resolution. 120s still gives 30 snapshots/hour/stream, i.e. ~210
samples per hour-of-day bucket over 7 days — far more than the ~40 that run 1's
analysis rested on. Halving the frequency to double the universe is the right
trade for this measurement.

FUNDING-INTERVAL CORRECTION (fetched live from Gate funding_interval / MEXC
collectCycle): 95 of these 129 settle every 4h, NOT the 8h the carry collector
hardcodes — their Phase-1 APR was understated ~2x. The ordering below is by
interval-corrected net-APR-on-capital at L=2x, H=30d, taker; it supersedes the
ranking in CARRY_CANDIDATES.md.

LEVELS = 50: BOTH venues reject 25 on the websocket depth channels ("provided
level not supported: 25" on Gate, "Not support limit" on MEXC) — they accept an
enum (5/10/20/50) and 50 is the nearest supported value above 25.
"""
from __future__ import annotations

# (exchange, symbol) — perp AND spot depth collected for each.
# Ordered by interval-corrected net30cap; the Phase-2 starter names are marked.
CARRY_BASKET: list[tuple[str, str]] = [
    ("gate", "WET_USDT"),            # 8h  net30cap  36.1%  # Phase-2 starter basket
    ("gate", "HANA_USDT"),           # 8h  net30cap  35.0%  # Phase-2 starter basket
    ("mexc", "PLAY_USDT"),           # 4h  net30cap  32.8%  # Phase-2 starter basket
    ("mexc", "H_USDT"),              # 4h  net30cap  32.7%
    ("mexc", "APR_USDT"),            # 4h  net30cap  31.8%
    ("gate", "IDOL_USDT"),           # 4h  net30cap  31.5%  # Phase-2 starter basket
    ("mexc", "BULLA_USDT"),          # 4h  net30cap  31.2%
    ("mexc", "PRL_USDT"),            # 4h  net30cap  25.4%
    ("gate", "ELSA_USDT"),           # 4h  net30cap  22.7%
    ("mexc", "IDOL_USDT"),           # 4h  net30cap  22.5%
    ("gate", "AIO_USDT"),            # 4h  net30cap  22.2%
    ("gate", "PTB_USDT"),            # 4h  net30cap  21.7%
    ("mexc", "TA_USDT"),             # 4h  net30cap  21.7%
    ("mexc", "ACU_USDT"),            # 4h  net30cap  21.4%
    ("mexc", "RIVER_USDT"),          # 4h  net30cap  21.0%
    ("gate", "STBL_USDT"),           # 4h  net30cap  18.7%
    ("mexc", "MAGMA_USDT"),          # 4h  net30cap  18.7%
    ("gate", "BTR_USDT"),            # 8h  net30cap  18.1%  # Phase-2 starter basket
    ("gate", "IN_USDT"),             # 4h  net30cap  18.1%
    ("gate", "TAKE_USDT"),           # 4h  net30cap  17.9%
    ("gate", "BLESS_USDT"),          # 4h  net30cap  16.4%
    ("gate", "ARIA_USDT"),           # 4h  net30cap  15.7%
    ("mexc", "VELVET_USDT"),         # 4h  net30cap  14.9%
    ("mexc", "BSB_USDT"),            # 4h  net30cap  14.2%
    ("mexc", "NIGHT_USDT"),          # 4h  net30cap  14.2%
    ("mexc", "FARTCOIN_USDT"),       # 4h  net30cap  13.7%
    ("gate", "INX_USDT"),            # 4h  net30cap  13.0%
    ("mexc", "EDGE_USDT"),           # 4h  net30cap  12.8%
    ("mexc", "B_USDT"),              # 4h  net30cap  12.6%
    ("mexc", "ZEREBRO_USDT"),        # 4h  net30cap  12.6%
    ("mexc", "PIEVERSE_USDT"),       # 4h  net30cap  11.8%
    ("mexc", "IN_USDT"),             # 4h  net30cap  10.5%
    ("mexc", "SKYAI_USDT"),          # 4h  net30cap   9.8%
    ("gate", "ZKP_USDT"),            # 4h  net30cap   8.7%
    ("gate", "LUMIA_USDT"),          # 4h  net30cap   8.5%
    ("mexc", "FF_USDT"),             # 4h  net30cap   8.0%
    ("gate", "PIEVERSE_USDT"),       # 8h  net30cap   7.2%
    ("gate", "O_USDT"),              # 4h  net30cap   7.1%
    ("gate", "OPG_USDT"),            # 4h  net30cap   7.0%
    ("gate", "SOPH_USDT"),           # 8h  net30cap   6.8%
    ("mexc", "BASED_USDT"),          # 4h  net30cap   6.7%
    ("mexc", "XVG_USDT"),            # 8h  net30cap   6.6%
    ("gate", "RESOLV_USDT"),         # 8h  net30cap   6.4%
    ("gate", "US_USDT"),             # 4h  net30cap   6.4%
    ("mexc", "ELSA_USDT"),           # 4h  net30cap   6.3%
    ("gate", "FF_USDT"),             # 4h  net30cap   5.8%
    ("mexc", "COAI_USDT"),           # 4h  net30cap   5.7%
    ("mexc", "ARX_USDT"),            # 4h  net30cap   5.5%
    ("gate", "ARC_USDT"),            # 4h  net30cap   5.3%
    ("gate", "LAB_USDT"),            # 4h  net30cap   5.2%
    ("mexc", "MANTA_USDT"),          # 4h  net30cap   5.0%
    ("mexc", "SPACE_USDT"),          # 4h  net30cap   5.0%
    ("mexc", "FOGO_USDT"),           # 4h  net30cap   4.9%
    ("gate", "UAI_USDT"),            # 8h  net30cap   4.9%
    ("gate", "LUNA_USDT"),           # 8h  net30cap   4.8%
    ("mexc", "WLFI_USDT"),           # 4h  net30cap   4.7%
    ("gate", "NAORIS_USDT"),         # 4h  net30cap   4.7%
    ("mexc", "SAPIEN_USDT"),         # 4h  net30cap   4.6%
    ("mexc", "MITO_USDT"),           # 4h  net30cap   4.6%
    ("mexc", "BANANAS31_USDT"),      # 4h  net30cap   4.6%
    ("gate", "XPIN_USDT"),           # 8h  net30cap   4.6%
    ("gate", "KGEN_USDT"),           # 4h  net30cap   4.6%
    ("gate", "AR_USDT"),             # 8h  net30cap   4.6%
    ("gate", "EVAA_USDT"),           # 8h  net30cap   4.5%
    ("mexc", "KITE_USDT"),           # 4h  net30cap   4.5%
    ("mexc", "HUMA_USDT"),           # 4h  net30cap   4.5%
    ("gate", "MYX_USDT"),            # 8h  net30cap   4.5%
    ("gate", "PLUME_USDT"),          # 8h  net30cap   4.4%
    ("mexc", "BEAMX_USDT"),          # 4h  net30cap   4.4%
    ("gate", "AXS_USDT"),            # 8h  net30cap   4.4%
    ("gate", "B_USDT"),              # 4h  net30cap   4.3%
    ("gate", "H_USDT"),              # 4h  net30cap   4.3%
    ("gate", "ATH_USDT"),            # 4h  net30cap   4.3%
    ("mexc", "FLOW_USDT"),           # 4h  net30cap   4.3%
    ("mexc", "VELODROME_USDT"),      # 4h  net30cap   4.3%
    ("gate", "MITO_USDT"),           # 4h  net30cap   4.2%
    ("gate", "JELLYJELLY_USDT"),     # 4h  net30cap   4.2%
    ("gate", "CKB_USDT"),            # 8h  net30cap   4.2%
    ("gate", "TOWNS_USDT"),          # 4h  net30cap   4.2%
    ("gate", "NOM_USDT"),            # 4h  net30cap   4.2%
    ("mexc", "SAGA_USDT"),           # 4h  net30cap   4.2%
    ("gate", "HOODX_USDT"),          # 8h  net30cap   4.2%
    ("gate", "GOAT_USDT"),           # 4h  net30cap   4.2%
    ("mexc", "SPX_USDT"),            # 4h  net30cap   4.1%
    ("gate", "SAHARA_USDT"),         # 8h  net30cap   4.1%
    ("mexc", "G_USDT"),              # 4h  net30cap   4.1%
    ("gate", "G_USDT"),              # 4h  net30cap   4.1%
    ("mexc", "PROMPT_USDT"),         # 4h  net30cap   4.1%
    ("mexc", "ATH_USDT"),            # 4h  net30cap   4.1%
    ("gate", "TAIKO_USDT"),          # 4h  net30cap   4.1%
    ("gate", "TURTLE_USDT"),         # 8h  net30cap   4.0%
    ("gate", "USTC_USDT"),           # 4h  net30cap   4.0%
    ("gate", "SCR_USDT"),            # 4h  net30cap   4.0%
    ("gate", "METIS_USDT"),          # 4h  net30cap   4.0%
    ("mexc", "YGG_USDT"),            # 4h  net30cap   4.0%
    ("gate", "COAI_USDT"),           # 8h  net30cap   4.0%
    ("mexc", "LQTY_USDT"),           # 8h  net30cap   4.0%
    ("gate", "SKY_USDT"),            # 4h  net30cap   3.9%
    ("mexc", "OG_USDT"),             # 4h  net30cap   3.9%
    ("mexc", "GRASS_USDT"),          # 4h  net30cap   3.9%
    ("mexc", "GENIUS_USDT"),         # 4h  net30cap   3.9%
    ("gate", "ORDER_USDT"),          # 4h  net30cap   3.9%
    ("mexc", "HEMI_USDT"),           # 4h  net30cap   3.9%
    ("gate", "ONT_USDT"),            # 4h  net30cap   3.9%
    ("mexc", "TURTLE_USDT"),         # 4h  net30cap   3.9%
    ("gate", "Q_USDT"),              # 4h  net30cap   3.8%
    ("gate", "LIGHT_USDT"),          # 8h  net30cap   3.8%
    ("mexc", "AT_USDT"),             # 4h  net30cap   3.8%
    ("gate", "ASTR_USDT"),           # 8h  net30cap   3.8%
    ("mexc", "CHR_USDT"),            # 8h  net30cap   3.8%
    ("mexc", "CTC_USDT"),            # 4h  net30cap   3.8%
    ("gate", "TREE_USDT"),           # 4h  net30cap   3.8%
    ("mexc", "BILL_USDT"),           # 4h  net30cap   3.8%
    ("gate", "HEMI_USDT"),           # 4h  net30cap   3.8%
    ("gate", "VELO_USDT"),           # 8h  net30cap   3.7%
    ("mexc", "OPG_USDT"),            # 4h  net30cap   3.7%
    ("mexc", "PORTAL_USDT"),         # 4h  net30cap   3.7%
    ("mexc", "BROCCOLI_USDT"),       # 4h  net30cap   3.6%
    ("gate", "WOO_USDT"),            # 8h  net30cap   3.5%
    ("gate", "MERL_USDT"),           # 8h  net30cap   3.4%
    ("gate", "PEOPLE_USDT"),         # 8h  net30cap   3.4%
    ("gate", "DOOD_USDT"),           # 8h  net30cap   3.3%
    ("mexc", "ACT_USDT"),            # 4h  net30cap   3.3%
    ("mexc", "SAHARA_USDT"),         # 4h  net30cap   3.2%
    ("gate", "SUPER_USDT"),          # 8h  net30cap   3.2%
    ("gate", "BAND_USDT"),           # 8h  net30cap   3.1%
    ("mexc", "DOGE_USDC"),           # 8h  net30cap   2.8%
    ("mexc", "BTC_USDT"),            # 8h  net30cap   2.6%  # Phase-2 starter basket
    ("gate", "STX_USDT"),            # 8h  net30cap   1.6%
]

GATE_SYMBOLS: list[str] = [s for ex, s in CARRY_BASKET if ex == "gate"]
MEXC_SYMBOLS: list[str] = [s for ex, s in CARRY_BASKET if ex == "mexc"]

# Top-N book levels stored per side. Must be a venue-supported enum value
# (5/10/20/50 on the WS depth channels) — 25 is REJECTED by both venues.
LEVELS = 50

# Symbols per websocket connection. Run 1 put all of a venue's names on ONE
# socket, so when that socket zombied the entire venue's perp data stopped at
# once. Chunking isolates the blast radius and keeps subscribe batches small
# enough that neither venue throttles the subscribe burst.
WS_CHUNK = 25


def spot_symbol(exchange: str, symbol: str) -> str:
    """Venue-native spot ticker for a perp symbol."""
    if exchange == "mexc":
        return symbol.replace("_", "")      # PLAY_USDT -> PLAYUSDT
    return symbol                            # gate spot uses HANA_USDT


def chunks(symbols: list[str], size: int = WS_CHUNK) -> list[list[str]]:
    return [symbols[i:i + size] for i in range(0, len(symbols), size)]
