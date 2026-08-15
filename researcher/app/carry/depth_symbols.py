"""Carry DEPTH-STUDY universe — the names we measure real capacity for.

THE POINT: Phase-1/2 showed carry APR is real (32-36% net on capital) but only
survives at EUR 50-225 per name — capacity, not funding, is the binding
constraint. Carry therefore scales by BREADTH, not position size. This universe
exists to test whether breadth can reach EUR 30k: 61 names x ~EUR 500 ~ EUR 30k.

SELECTION (61 of the 129 coin-venues that passed the Phase-1 gates):
ranked by net-APR-on-capital at L=2x, H=30d, taker, AFTER correcting each coin's
REAL funding interval, then the top 60 taken plus mexc BTC_USDT force-added as
ballast/control. Cutoff was net30cap >= 4.6%.

WHY 60 AND NOT ALL 129: disk. At 50 levels x 2 markets x 60s, 129 names project
~5.1 GB/day; 61 names project ~2.4 GB/day, inside the 2-3 GB/day budget with
48h of headroom on 52 GB free. 61 names is already enough to answer the EUR 30k
breadth question.

FUNDING-INTERVAL CORRECTION (fetched live from Gate funding_interval / MEXC
collectCycle): 51 of these 61 names settle every 4h, NOT the 8h the carry
collector hardcodes — their Phase-1 APR was understated ~2x. Across the full
129-name shortlist it was 95/129. The Phase-1 ranking in CARRY_CANDIDATES.md is
therefore materially wrong and is superseded by the ordering below.

LEVELS = 50, not the 25 originally specified: BOTH venues reject 25 on the
websocket depth channels ("provided level not supported: 25" on Gate,
"Not support limit" on MEXC) — they accept an enum, and 20/50 are the
neighbours. 50 is the nearest supported value above 25 and kills the top-10
truncation that made IDOL/PLAY read "BOOK OUT" under EUR 250 more decisively.
"""
from __future__ import annotations

# (exchange, symbol) — perp AND spot depth collected for each
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
    ("mexc", "BTC_USDT"),            # 8h  net30cap   2.6%  # Phase-2 starter basket
]

GATE_SYMBOLS: list[str] = [s for ex, s in CARRY_BASKET if ex == "gate"]
MEXC_SYMBOLS: list[str] = [s for ex, s in CARRY_BASKET if ex == "mexc"]

# Top-N book levels stored per side. Must be a venue-supported enum value
# (5/10/20/50 on the WS depth channels) — 25 is REJECTED by both venues.
LEVELS = 50


def spot_symbol(exchange: str, symbol: str) -> str:
    """Venue-native spot ticker for a perp symbol."""
    if exchange == "mexc":
        return symbol.replace("_", "")      # PLAY_USDT -> PLAYUSDT
    return symbol                            # gate spot uses HANA_USDT
