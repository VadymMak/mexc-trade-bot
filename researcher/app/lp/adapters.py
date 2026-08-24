"""Adapter for the stable-pair LP fee-yield collector.

READ-ONLY PUBLIC ENDPOINT ONLY. No keys, no orders.

One source today: the DefiLlama yields API, already proven reachable by the
lending collector (candidate #3) and requiring no key and no RPC node. The
Adapter base class carries its own cadence and its own failure isolation so a
second source can be added later without touching the loop.

WHY DefiLlama AND NOT PER-DEX SUBGRAPHS
    Fee APR needs fees over a window divided by liquidity over the same window.
    Computing that per DEX means a subgraph per DEX, a fee-tier table per DEX,
    and a different definition of "volume" per DEX. DefiLlama already does that
    normalisation across ~35 venues, and it publishes apyBase and apyReward
    SEPARATELY — which is the one property this whole exercise depends on. The
    cost is that we inherit its errors, which is why this package cross-checks
    apyBase against turnover and flags disagreement rather than trusting it.

CADENCE. One ~11 MB pull per cycle at 1800s, matching DefiLlama's own update
weight. Fee APR is a trailing-window quantity; polling it faster returns the
same number and only burns their bandwidth.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .common import (COLUMNS, MIN_TVL_USD, TOP_N,
                     TVL_IMPLAUSIBLE_USD, Row, base_vs_volume, classify_pool,
                     f, gather_isolated, is_concentrated, screen,
                     venue_kind_of)

log = logging.getLogger("lp")


class Adapter:
    """Base. `cadence_secs` lets a heavy source run slower than the main loop."""

    name = "?"
    cadence_secs = 0.0            # 0 = every cycle
    _last_run: float | None = None

    def due(self, t0: float) -> bool:
        return (self.cadence_secs <= 0 or self._last_run is None
                or t0 - self._last_run >= self.cadence_secs)

    def mark(self, t0: float) -> None:
        self._last_run = t0

    async def snapshot(self, session) -> tuple[list[Row], dict, list]:
        raise NotImplementedError


class DefiLlamaStablePools(Adapter):
    """Stable-STABLE LP pools above a TVL floor, ranked by TVL.

    THE UNIVERSE FILTER, and why each clause is load-bearing (all measured
    against the live payload on 2026-08-24):

        stablecoin == True        DefiLlama's own stable-exposure flag.
                                  3035 pools carry it; 968 above $1M TVL.
        exposure == 'multi'       THE critical cut. 746 of those 968 are
                                  exposure='single' — aave-v3, morpho-blue,
                                  pendle, yearn. Those are LENDING markets,
                                  which is candidate #3 and already collected.
                                  Without this clause two thirds of the rows
                                  would be a duplicate of another collector
                                  wearing an LP label. 968 -> 222.
        tvlUsd >= MIN_TVL_USD     A fee APR on a $50k pool is an artifact of
                                  one trade, not a yield.

    What is NOT filtered out, and is flagged instead: FX pairs, yield-bearing
    legs, wrappers and implausible TVL. Those rows are the evidence that the
    naive reading of this dataset is wrong, so they are recorded with their
    flags rather than deleted. See common.py for the measured numbers.

    RANKING. On tvlUsd, descending, after excluding rows flagged
    tvl_implausible — otherwise the single fluid-dex USDC-CSUSDL row
    ($16.4bn published against volumeUsd1d=0 and one datapoint of history)
    takes rank 1 and is 96% of the universe by TVL. Flagged rows are written
    with rank NULL, which is a statement that they were seen and not ranked.
    """

    name = "defillama"
    cadence_secs = 1800.0
    POOLS = "https://yields.llama.fi/pools"

    async def snapshot(self, session):
        (payload,), failures = await gather_isolated(session, [self.POOLS])
        stats: dict = {}
        if payload is None:
            return [], {"missing_payload": 1}, failures

        data = payload.get("data") if isinstance(payload, dict) else payload
        if not data:
            stats["empty_payload"] = 1
            log.error("[lp/defillama] payload parsed but carried no pools — "
                      "writing nothing rather than an empty universe")
            return [], stats, failures

        stats["pools_seen"] = len(data)
        flagged = [p for p in data if p.get("stablecoin")]
        stats["stable_flagged"] = len(flagged)
        # The exposure='multi' cut is what separates LP from lending. Counted
        # explicitly so the health table shows the split rather than only the
        # survivors — if DefiLlama ever renames the field, this number goes to
        # zero and says so loudly instead of the universe silently emptying.
        lp = [p for p in flagged if p.get("exposure") == "multi"]
        stats["lp_multi"] = len(lp)
        stats["single_exposure_lending"] = len(flagged) - len(lp)
        universe = [p for p in lp if (f(p.get("tvlUsd")) or 0.0) >= MIN_TVL_USD]
        stats["above_tvl_floor"] = len(universe)
        stats["below_tvl_floor"] = len(lp) - len(universe)
        if not universe:
            log.error("[lp/defillama] 0 pools passed stablecoin+multi+TVL>=%.0f "
                      "— upstream shape probably changed", MIN_TVL_USD)
            return [], stats, failures

        observed_at = datetime.now(timezone.utc)
        built: list[tuple[float, Row]] = []
        for p in universe:
            tvl = f(p.get("tvlUsd"))
            apy_base = f(p.get("apyBase"))
            apy_reward = f(p.get("apyReward"))
            vol1d = f(p.get("volumeUsd1d"))
            cls = classify_pool(p.get("symbol") or "")
            verdict, turnover, implied = base_vs_volume(apy_base, tvl, vol1d)
            implausible = bool(tvl and tvl >= TVL_IMPLAUSIBLE_USD
                               and not (vol1d or 0.0))
            project = p.get("project") or ""
            kind = venue_kind_of(project)

            if implausible:
                stats["tvl_implausible"] = stats.get("tvl_implausible", 0) + 1
                log.warning("[lp/defillama] TVL IMPLAUSIBLE %s %s %s: $%.0f with "
                            "volumeUsd1d=%s and %s datapoints — written and "
                            "flagged, excluded from ranking", project,
                            p.get("chain"), p.get("symbol"), tvl or 0.0,
                            p.get("volumeUsd1d"), p.get("count"))
            if cls["same_peg"] is False:
                stats["mixed_peg_fx"] = stats.get("mixed_peg_fx", 0) + 1
            if cls["same_peg"] is None:
                stats["peg_unknown"] = stats.get("peg_unknown", 0) + 1
            if cls["has_yield_bearing_leg"]:
                stats["yield_bearing"] = stats.get("yield_bearing", 0) + 1
            if kind == "wrapper":
                stats["wrapper"] = stats.get("wrapper", 0) + 1
            if kind == "non_dex":
                stats["non_dex"] = stats.get("non_dex", 0) + 1
            if verdict == "base_understated":
                stats["base_understated"] = stats.get("base_understated", 0) + 1

            row = Row(
                pool_id=p.get("pool"), project=project, chain=p.get("chain"),
                symbol=p.get("symbol"), legs=cls["legs"], n_legs=cls["n_legs"],
                venue_kind=kind,
                # THE SEPARATION. Three columns, never summed. apy_total is
                # stored only so the published headline can be reproduced and
                # shown to be mostly subsidy.
                apy_base=apy_base, apy_reward=apy_reward,
                apy_total=f(p.get("apy")),
                apy_base_7d=f(p.get("apyBase7d")),
                apy_mean_30d=f(p.get("apyMean30d")),
                tvl_usd=tvl, volume_usd_1d=vol1d,
                volume_usd_7d=f(p.get("volumeUsd7d")),
                turnover_1d=turnover, implied_apr_1bp=implied,
                apy_base_vs_volume=verdict,
                peg_currency=cls["peg_currency"], same_peg=cls["same_peg"],
                has_yield_bearing_leg=cls["has_yield_bearing_leg"],
                yield_bearing_legs=cls["yield_bearing_legs"],
                unclassified_legs=cls["unclassified_legs"],
                concentrated_liquidity=is_concentrated(project),
                is_wrapper=(kind == "wrapper"),
                tvl_implausible=implausible,
                il_risk_llama=p.get("ilRisk"),
                sigma=f(p.get("sigma")), mu=f(p.get("mu")),
                outlier=bool(p.get("outlier")),
                datapoints=(int(p["count"]) if isinstance(p.get("count"), (int, float))
                            else None),
                apy_pct_1d=f(p.get("apyPct1D")),
                apy_pct_7d=f(p.get("apyPct7D")),
                apy_pct_30d=f(p.get("apyPct30D")),
                rank_by_tvl=None,          # assigned after the implausible cut
                pool_meta=p.get("poolMeta"),
                endpoint=self.POOLS, observed_at=observed_at,
                # extra carries ROW-SPECIFIC upstream fields only. The
                # standing caveats (never sum base+reward, CLMM APR is
                # pool-level, depeg tail risk) are properties of the whole
                # dataset, not of a row: they are stated once as COMMENTs on
                # the table and its columns, and logged at every startup.
                # Repeating them in all ~9,600 rows a day would have made the
                # prose three quarters of the stored bytes.
                extra=json.dumps({
                    "rewardTokens": p.get("rewardTokens"),
                    "underlyingTokens": p.get("underlyingTokens"),
                    "exposure": p.get("exposure"),
                    "apyBaseInception": p.get("apyBaseInception"),
                    "il7d": p.get("il7d"),
                    "classify_rules": cls["classify_rules"],
                }, default=str),
            )
            if screen(row, stats):
                built.append((tvl or 0.0, row))

        # Rank on plausible TVL only, then keep the top N.
        rankable = sorted((x for x in built if not x[1].tvl_implausible),
                          key=lambda x: -x[0])
        for i, (_, row) in enumerate(rankable, start=1):
            row.rank_by_tvl = float(i)
        kept = [r for _, r in rankable[:TOP_N]]
        kept += [r for _, r in built if r.tvl_implausible]
        stats["ranked"] = len(rankable)
        stats["dropped_beyond_top_n"] = max(0, len(rankable) - TOP_N)
        if stats["dropped_beyond_top_n"]:
            # Never a silent cap: say what fell off and at what TVL.
            log.info("[lp/defillama] top-%d cap dropped %d ranked pools below "
                     "$%.0f TVL", TOP_N, stats["dropped_beyond_top_n"],
                     rankable[TOP_N][0] if len(rankable) > TOP_N else 0.0)
        return kept, stats, failures


ADAPTERS = (DefiLlamaStablePools,)

assert len(COLUMNS) == len(set(COLUMNS)), "duplicate column name"
