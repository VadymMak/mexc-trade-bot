"""Shared plumbing for the STABLE-PAIR LP (liquidity-provision) fee-yield collector.

READ-ONLY, PUBLIC ENDPOINTS ONLY. No keys, no orders, no private call path.

WHAT THIS MEASURES — and why it is the most ORTHOGONAL of the four candidates
    Perp funding, dated basis and stablecoin lending are all prices of the same
    underlying thing: demand for LEVERAGE / CREDIT. LP fee yield is paid out of
    TRADING VOLUME. Its driver is turnover and volatility, not borrow demand, so
    it is the first candidate that can plausibly be uncorrelated with the other
    three. Whether it actually is remains a question for the series, not this
    file — which is why this is a collector and not a one-off read.

    Stable-STABLE pools specifically, because when both legs are pegged to the
    same unit the impermanent loss is near zero, so the fee yield is close to the
    realisable return. That premise is load-bearing, and this module spends most
    of its length checking that it actually holds for each row.

THE SEPARATION THAT IS THE WHOLE POINT
    apy_base    REAL swap-fee yield — paid by traders, the structural edge
    apy_reward  token incentives — minted, temporary, a SUBSIDY not an edge
    apy_total   what DefiLlama publishes as the headline
    They are stored in three separate columns and NEVER summed into one. A pool
    paying 2% in fees and 20% in farm tokens is a 2% edge with a 20% subsidy
    that ends. Measured 2026-08-24, convex PMUSD-CRVUSD published apyBase 0.00
    and apyReward 22.93 — a 23% headline on a 0% edge.

FIVE WAYS THIS DATASET LIES IF YOU DO NOT SCREEN IT
    Each of these was measured against the live /pools payload on 2026-08-24,
    and each gets a stored flag rather than a silent drop.

    1. DefiLlama's `stablecoin: true` flag INCLUDES LENDING pools. 968 pools
       carry it above $1M TVL, but 746 of them are exposure='single' — aave-v3,
       morpho-blue, pendle, yearn. Those are candidate #3, already collected.
       LP requires exposure='multi'. That cut alone is 968 -> 222.

    2. NON-USD stables are flagged `stablecoin` too, and they carry real FX
       risk — a USDC/EURC pool has genuine impermanent loss when EURUSD moves.
       This matters enormously because the FX pairs sit at the TOP of the
       apyBase ranking: gmtrade USDCAD-USDC published apyBase 56.9%, EUR-USDC
       55.5%, aerodrome EURC-USDC 24.0%. Rank the universe naively by fee yield
       and the entire top of the list is FX carry wearing a stablecoin label.
       So every leg is classified to a PEG CURRENCY and `same_peg` records
       whether the near-zero-IL premise actually holds. DefiLlama's own ilRisk
       field does not catch this: USDCAD-USDC reports ilRisk='no' with
       sigma=0.85.

    3. YIELD-BEARING legs contaminate apyBase. sUSDe, sDAI, scrvUSD, csUSDL,
       aTokens and syrupUSDC appreciate against their base by design, and that
       appreciation lands in the pool's measured return. It is real yield but it
       is NOT swap-fee yield, and it is already counted in candidate #3. Flagged
       per row with the legs named.

    4. WRAPPERS double-count. convex-finance, stake-dao, yearn-finance and beefy
       re-list Curve pools as their own. Measured: convex SFRXUSD-FRXUSD TVL
       $11,861,022 against curve's $11,861,043 — the same dollars to within $21.
       Summing TVL across projects would double-count, and a wrapper's apyBase
       is not even the same quantity: yearn published 5.75% for the DOLA-SUSDE
       pool whose Curve apyBase is 1.06%, because the vault folds compounded
       rewards into its base. Flagged, and excluded from the DEX-only view.

    5. TVL can be nonsense. fluid-dex USDC-CSUSDL published $16,443,178,618 —
       96% of the entire stable-LP universe by TVL — with volumeUsd1d=0 and
       count=1 (one datapoint of history). Flagged `tvl_implausible` and given
       no rank, but still WRITTEN, because deleting the anomaly is how a dataset
       loses the evidence that it has one.

TWO HONEST FLAGS THE PROMPT ASKED FOR BY NAME
    concentrated_liquidity — for Uniswap v3 and every other CLMM, DefiLlama's
        APR is POOL-LEVEL. A real LP picks a range; inside a tight range the
        yield is higher and outside it is zero. The published number is not a
        personal-range yield and is stored as such.
    depeg_tail_risk — the real tail for stable LP is one leg leaving $1. An AMM
        automatically sells the survivor for the failing asset, so the LP ends
        up holding the broken one. It is the LP analogue of adverse selection:
        you are filled precisely when you are wrong. Not a number, so it is
        carried as a per-row note rather than pretended into a column.

CROSS-CHECK, NOT TRUST. apyBase is checked against turnover (volumeUsd1d/TVL).
Measured: curve DAI-USDC-USDT published apyBase=0.00 on $10,589,778 of daily
volume against $160,498,433 TVL — 6.6% daily turnover, which at the 3pool's 1bp
fee implies 0.241% APR, not zero. So apyBase can be stale or rounded to nothing.
`apy_base_vs_volume` records agree / base_understated / no_volume so the
disagreement is a column instead of a surprise.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiohttp

log = logging.getLogger("lp")

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=60)
FETCH_RETRIES = 3
FETCH_BACKOFF_SECS = (2.0, 8.0, 20.0)

# ── Universe gates ──────────────────────────────────────────────────────────
MIN_TVL_USD = 1_000_000.0        # below this, fee yield is noise on a dead pool
TOP_N = 200                      # 222 pools clear the floor today; keep ~all
TVL_IMPLAUSIBLE_USD = 5_000_000_000.0   # see failure mode 5 above

# apyBase is a SWAP-FEE yield. A stable-stable pool earning >100% of TVL in
# fees annually is a units or accounting failure, not a fee level.
MIN_APY_BASE, MAX_APY_BASE = -1.0, 100.0
# Rewards legitimately reach triple digits when a token is being emitted hard,
# so this bound is deliberately loose — it catches broken units, not generosity.
MIN_APY_REWARD, MAX_APY_REWARD = -1.0, 5000.0

# ── Peg classification (failure mode 2) ─────────────────────────────────────
# Explicit and hand-checked rather than inferred from the ticker, because the
# ticker actively misleads: gmtrade's USDCAD / USDCHF / USDMXN are FX pair
# tokens whose names begin with "USD" while their peg is CAD / CHF / MXN.
NON_USD_PEG: dict[str, str] = {
    "EUR": "EUR", "EURC": "EUR", "EURS": "EUR", "EUROC": "EUR",
    "EURCV": "EUR", "EURW": "EUR", "SEUR": "EUR", "EUSX": "EUR",
    "AGEUR": "EUR", "EURA": "EUR", "EURE": "EUR",
    "ZCHF": "CHF", "USDCHF": "CHF",
    "USDMXN": "MXN", "USDCAD": "CAD",
    "XAUT": "XAU", "PAXG": "XAU",
}

# Plain USD-pegged stables: claim to hold $1 and do not accrue to the holder.
USD_PEG: frozenset[str] = frozenset({
    "USDC", "USDT", "USDT0", "USDTB", "DAI", "FRAX", "FRXUSD", "WFRAX",
    "CRVUSD", "USDE", "PYUSD", "USDG", "AUSD", "RLUSD", "GHO", "DOLA",
    "USDS", "BOLD", "USD0", "USD1", "USD3", "ALUSD", "FXUSD", "USDF",
    "USX", "MSUSD", "APXUSD", "APYUSD", "REUSD", "TRUSD", "PMUSD",
    "USDM", "USDB", "EUSD", "DUSD", "HYUSD", "AVUSD", "USDU", "JUPUSD",
    "USDAT", "BUCK", "CASH", "FPI", "MUSD", "USN", "OUSD", "USDA",
    "USC", "USDSUI", "VDUSD", "FIDD", "MIM", "TUSD", "USDP", "FDUSD",
    "LUSD", "SUSD", "USDD", "BUSD", "BUSD0", "USDCV", "EVAUSDT",
    "EVAUSDC", "GHUSDC", "VGUSDC", "FUSDC", "WNUSDC", "WNUSDT0",
    "WNAUSD", "FTUSD", "STRUSD", "USAT", "UNO", "RAIN", "FRAXBP",
    "SMSUSD", "USDY0", "DEUSD", "USDX", "USDL", "NUSD", "MKUSD",
})

# Legs that ACCRUE (failure mode 3): their price rises against the base by
# design, so pool return includes yield that is not a swap fee.
YIELD_BEARING: frozenset[str] = frozenset({
    "SUSDE", "SUSDS", "SDAI", "SFRXUSD", "SCRVUSD", "SDOLA", "SUSDAI",
    "SUSDA", "SUSN", "SFRAX", "SUSDX", "SDEUSD",
    "CSUSDL", "STUSDS", "STKGHO", "USDY", "RUSDY", "USD0++", "WSTUSR",
    "SYRUPUSDC", "SYRUPUSDT", "SYRUPUSDG", "RE7SCUSD", "SCUSD",
    # Interest-bearing money-market receipts (Aave / Compound style).
    "AMDAI", "AMUSDC", "AMUSDT", "AVDAI", "AVUSDC", "AVUSDT", "AUSDT",
    "AUSDC", "ADAI", "IDAI", "IUSDC", "IUSDT", "CUSDC", "CDAI",
})

# Projects that re-list another protocol's pool (failure mode 4).
WRAPPER_PROJECTS: frozenset[str] = frozenset({
    "convex-finance", "stake-dao", "yearn-finance", "beefy", "aura",
    "concentrator", "pickle", "harvest-finance", "magpie", "penpie",
})

# Projects that are not AMMs at all but leak through exposure='multi'.
NON_DEX_PROJECTS: frozenset[str] = frozenset({
    "inverse-finance-firm", "fluid-lending", "curve-llamalend",
    "morpho-blue", "aave-v3", "aave-v4", "pendle", "midas-rwa",
})

# CLMM / range-based AMMs: the published APR is POOL-LEVEL, not the yield any
# individual LP earns, because that depends on the range they chose.
CONCENTRATED_PROJECTS: frozenset[str] = frozenset({
    "uniswap-v3", "uniswap-v4", "pancakeswap-amm-v3", "aerodrome-slipstream",
    "velodrome-slipstream", "camelot-v3", "orca-dex", "kamino-liquidity",
    "cetus-clmm", "raydium-clmm", "bluefin-spot", "hyperion", "thalaswap",
    "mosaic-amm", "vvs-flawless", "shadow-exchange", "ramses-v2",
    "quickswap-v3", "sushiswap-v3", "algebra", "izumi-finance",
    "traderjoe-dex", "fluid-dex", "nostra-pools", "hydration-dex",
})
_CONCENTRATED_HINTS = ("-v3", "-v4", "clmm", "slipstream")

DEPEG_NOTE = (
    "Tail risk for a stable LP is a DEPEG: if one leg leaves $1 the AMM "
    "automatically sells the sound asset for the failing one, so the LP is left "
    "holding the broken leg. It is the LP analogue of adverse selection — you "
    "are filled exactly when you are wrong — and no APR column prices it."
)


def f(v: Any) -> Optional[float]:
    """Parse to float, None on missing/blank/unparseable — never fabricate."""
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x or x in (float("inf"), float("-inf")) else x


def split_legs(symbol: str) -> list[str]:
    """DefiLlama writes pool symbols as 'USDC-USDT' or 'DAI/USDC/USDT'."""
    if not symbol:
        return []
    s = symbol.upper().replace("/", "-").replace("+", "-")
    return [p.strip() for p in s.split("-") if p.strip()]


def classify_leg(leg: str) -> tuple[Optional[str], bool, str]:
    """Return (peg_currency, is_yield_bearing, rule).

    `rule` names WHICH test matched so a classification can be audited later
    instead of taken on faith. An unrecognised leg returns peg=None — reported
    as UNKNOWN, never quietly assumed to be a dollar.
    """
    if leg in NON_USD_PEG:                       # checked FIRST: see USDCAD
        return NON_USD_PEG[leg], False, "non_usd_explicit"
    if leg in YIELD_BEARING:
        return "USD", True, "yield_bearing_explicit"
    if leg in USD_PEG:
        return "USD", False, "usd_explicit"
    # Principled, self-limiting derivation: a leading S over a KNOWN plain
    # stable is the standard savings-wrapper naming (sDAI over DAI, sUSDe over
    # USDe). Deliberately not generalised to A/I/W prefixes, which would
    # misread AUSD (Agora's plain dollar) as an Aave receipt.
    if leg.startswith("S") and leg[1:] in USD_PEG:
        return "USD", True, "yield_bearing_s_prefix"
    if leg.startswith("W") and leg[1:] in USD_PEG:
        return "USD", False, "wrapped_w_prefix"
    return None, False, "unclassified"


def classify_pool(symbol: str) -> dict:
    """Peg / yield-bearing analysis for a pool's legs.

    same_peg is deliberately TRISTATE. True means the near-zero-IL premise
    holds; False means it provably does not (a mixed-currency pool); None means
    a leg could not be classified and we do not know. Collapsing None into
    False would hide new tokens, and into True would assert the premise on no
    evidence.
    """
    legs = split_legs(symbol)
    pegs, yb, rules, unknown = [], [], [], []
    for leg in legs:
        peg, is_yb, rule = classify_leg(leg)
        rules.append(f"{leg}:{rule}")
        if peg is None:
            unknown.append(leg)
        else:
            pegs.append(peg)
        if is_yb:
            yb.append(leg)
    distinct = sorted(set(pegs))
    if unknown:
        peg_currency, same_peg = "UNKNOWN", None
    elif len(distinct) == 1:
        peg_currency, same_peg = distinct[0], True
    else:
        peg_currency, same_peg = "MIXED:" + "/".join(distinct), False
    return {
        "legs": "-".join(legs),
        "n_legs": len(legs),
        "peg_currency": peg_currency,
        "same_peg": same_peg,
        "has_yield_bearing_leg": bool(yb),
        "yield_bearing_legs": ",".join(yb) or None,
        "unclassified_legs": ",".join(unknown) or None,
        "classify_rules": "; ".join(rules),
    }


def is_concentrated(project: str) -> bool:
    p = (project or "").lower()
    return p in CONCENTRATED_PROJECTS or any(h in p for h in _CONCENTRATED_HINTS)


def venue_kind_of(project: str) -> str:
    p = (project or "").lower()
    if p in WRAPPER_PROJECTS:
        return "wrapper"
    if p in NON_DEX_PROJECTS:
        return "non_dex"
    return "dex"


def base_vs_volume(apy_base: Optional[float], tvl: Optional[float],
                   vol1d: Optional[float]) -> tuple[str, Optional[float], Optional[float]]:
    """Cross-check the published fee APR against observed turnover.

    Returns (verdict, turnover_1d, implied_apr_at_1bp). We cannot compute the
    true implied fee APR because DefiLlama does not publish the pool's fee tier,
    so 1bp is used as an explicit LOWER-BOUND reference for stable pools (the
    Curve 3pool and the Uniswap USDC/USDT 0.01% tier both sit there). The point
    is not to replace apyBase but to notice when it disagrees with the tape.
    """
    if not tvl or vol1d is None:
        return "no_volume", None, None
    turnover = vol1d / tvl
    implied = turnover * 0.0001 * 365.0 * 100.0
    if apy_base is None:
        return "no_base", turnover, implied
    # Flag only a material gap: a published base below half of what a 1bp fee
    # on observed volume would already pay.
    if implied > 0.05 and apy_base < implied * 0.5:
        return "base_understated", turnover, implied
    return "agree", turnover, implied


class FetchError(Exception):
    """One endpoint failed every retry. Names the URL so a degraded cycle says
    WHICH feed is down instead of logging an anonymous traceback."""

    def __init__(self, url: str, cause: BaseException) -> None:
        super().__init__(f"{url}: {cause!r}")
        self.url, self.cause = url, cause


async def get_json(session: aiohttp.ClientSession, url: str,
                   retries: int | None = None) -> Any:
    """The ONLY HTTP call site in this package. Raises FetchError on give-up,
    never a sentinel — an empty payload reads downstream as 'there are no stable
    pools', which is how a hole gets written into history as if it were data."""
    attempts = FETCH_RETRIES if retries is None else retries
    last: BaseException | None = None
    for i in range(attempts):
        try:
            async with session.get(url, timeout=HTTP_TIMEOUT,
                                   headers={"User-Agent": "Mozilla/5.0"}) as r:
                r.raise_for_status()
                return await r.json(content_type=None)
        except Exception as exc:                  # noqa: BLE001 — re-raised below
            last = exc
            if i + 1 < attempts:
                await asyncio.sleep(FETCH_BACKOFF_SECS[min(i, len(FETCH_BACKOFF_SECS) - 1)])
    raise FetchError(url, last) from last


async def gather_isolated(session: aiohttp.ClientSession,
                          urls: list[str]) -> tuple[list, list]:
    """Fetch several endpoints INDEPENDENTLY. One dead feed must not discard the
    cycle. Returns (results, failures) with a failed feed as None."""
    res = await asyncio.gather(*(get_json(session, u) for u in urls),
                              return_exceptions=True)
    out, failures = [], []
    for url, r in zip(urls, res):
        if isinstance(r, BaseException):
            out.append(None)
            failures.append(r if isinstance(r, FetchError) else FetchError(url, r))
        else:
            out.append(r)
    return out, failures


# Column order here IS the INSERT order; both live in this module so they cannot
# drift apart. apy_base / apy_reward / apy_total are three separate columns on
# purpose and are never summed anywhere in this package.
COLUMNS = (
    "pool_id", "project", "chain", "symbol", "legs", "n_legs", "venue_kind",
    "apy_base", "apy_reward", "apy_total", "apy_base_7d", "apy_mean_30d",
    "tvl_usd", "volume_usd_1d", "volume_usd_7d", "turnover_1d",
    "implied_apr_1bp", "apy_base_vs_volume",
    "peg_currency", "same_peg", "has_yield_bearing_leg", "yield_bearing_legs",
    "unclassified_legs", "concentrated_liquidity", "is_wrapper",
    "tvl_implausible", "il_risk_llama", "sigma", "mu", "outlier",
    "datapoints", "apy_pct_1d", "apy_pct_7d", "apy_pct_30d",
    "rank_by_tvl", "pool_meta", "endpoint", "observed_at", "extra",
)


class Row:
    __slots__ = tuple(c for c in COLUMNS)

    def __init__(self, **kw) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, c) for c in COLUMNS)


def screen(row: Row, stats: dict) -> bool:
    """Gate a row before it is written. Every rejection is COUNTED so an upstream
    shape change shows up in the health table instead of looking like a market
    that went quiet.

    Note what is NOT screened out: a flagged row (FX pair, yield-bearing leg,
    wrapper, implausible TVL) is still WRITTEN. Those are analysis flags, not
    data errors — dropping them would destroy the evidence that the naive
    reading of this dataset is wrong. Only broken NUMBERS are rejected.
    """
    if row.tvl_usd is None:
        stats["missing_tvl"] = stats.get("missing_tvl", 0) + 1
        return False
    if row.apy_base is None and row.apy_reward is None and row.apy_total is None:
        stats["no_apy"] = stats.get("no_apy", 0) + 1
        return False
    if row.apy_base is not None and not (MIN_APY_BASE <= row.apy_base <= MAX_APY_BASE):
        stats["base_out_of_range"] = stats.get("base_out_of_range", 0) + 1
        log.warning("[lp] RANGE SKIP %s %s %s: apyBase=%.4f outside [%.0f, %.0f]"
                    " — a swap fee cannot pay that; units may have changed",
                    row.project, row.chain, row.symbol, row.apy_base,
                    MIN_APY_BASE, MAX_APY_BASE)
        return False
    if row.apy_reward is not None and not (MIN_APY_REWARD <= row.apy_reward <= MAX_APY_REWARD):
        stats["reward_out_of_range"] = stats.get("reward_out_of_range", 0) + 1
        return False
    return True
