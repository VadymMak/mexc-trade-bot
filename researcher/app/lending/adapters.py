"""Per-source adapters for the stablecoin lending/borrow rate collector.

READ-ONLY PUBLIC ENDPOINTS ONLY. No keys, no orders.

Each adapter is ISOLATED: its own endpoints, its own failure counters, its own
cadence. A source that errors, times out or changes shape removes only its own
rows for that cycle. Within a source the endpoints are isolated from each other
too, so a dead supply feed does not discard the borrow feed.

COVERAGE (probed 2026-08-24). See AUTH_SKIPPED in common.py for the gaps and
why each one is a gap rather than an omission.
    okx      supply + borrow
    bybit    borrow only
    kucoin   supply only
    binance  borrow only
    aave     supply + borrow  (v3 and v4, Ethereum, via the DefiLlama yields API)
"""
from __future__ import annotations

import json
import logging

import aiohttp

from .common import (ASSETS, Row, f, gather_isolated, get_json, screen,
                     ts_from_compact)

log = logging.getLogger("lending")


class Adapter:
    """Base. `cadence_secs` lets a heavy source run slower than the main loop
    without slowing the cheap ones down."""

    name = "?"
    venue_kind = "cex"
    cadence_secs = 0.0            # 0 = every cycle
    _last_run: float | None = None

    def due(self, t0: float) -> bool:
        return (self.cadence_secs <= 0 or self._last_run is None
                or t0 - self._last_run >= self.cadence_secs)

    def mark(self, t0: float) -> None:
        self._last_run = t0

    async def snapshot(self, session) -> tuple[list[Row], dict, list]:
        raise NotImplementedError


# ── OKX — supply (flexible savings) + borrow (margin quota) ─────────────────
class OKX(Adapter):
    """Both sides from OKX.

    Supply: finance/savings/lending-rate-summary publishes estRate (the forward
    estimate), avgRate and preRate as ANNUAL FRACTIONS. estRate is the headline;
    the other two are kept in `extra` because preRate is what corroborates the
    borrow-side units.

    Borrow: public/interest-rate-loan-quota `basic` tier. The rate is DAILY —
    see the corroboration note in common.py. `basic` is the retail tier, which
    is the one that matters if this ever becomes a real allocation.
    """

    name = "okx"
    SUPPLY = "https://www.okx.com/api/v5/finance/savings/lending-rate-summary?ccy={}"
    BORROW = "https://www.okx.com/api/v5/public/interest-rate-loan-quota"

    async def snapshot(self, session):
        urls = [self.SUPPLY.format(a) for a in ASSETS] + [self.BORROW]
        res, failures = await gather_isolated(session, urls)
        stats: dict = {}
        rows: list[Row] = []

        for a, d in zip(ASSETS, res[:len(ASSETS)]):
            if d is None:
                stats["missing"] = stats.get("missing", 0) + 1
                continue
            for item in (d.get("data") or []):
                r = Row(source=self.name, venue_kind=self.venue_kind, asset=a,
                        rate_type="supply", raw_rate=f(item.get("estRate")),
                        raw_basis="annual_fraction", rate_field="estRate",
                        tier="flexible-savings", term="flexible",
                        endpoint=self.SUPPLY.format(a),
                        extra=json.dumps({"avgRate": item.get("avgRate"),
                                          "preRate": item.get("preRate")}))
                if screen(r, stats):
                    rows.append(r)

        d = res[-1]
        if d is None:
            stats["missing"] = stats.get("missing", 0) + 1
        else:
            basic = ((d.get("data") or [{}])[0]).get("basic") or []
            for item in basic:
                if item.get("ccy") not in ASSETS:
                    continue
                r = Row(source=self.name, venue_kind=self.venue_kind,
                        asset=item["ccy"], rate_type="borrow",
                        raw_rate=f(item.get("rate")), raw_basis="daily",
                        rate_field="basic[].rate", tier="basic", term=None,
                        endpoint=self.BORROW,
                        extra=json.dumps({"quota": item.get("quota")}))
                if screen(r, stats):
                    rows.append(r)
        return rows, stats, failures


# ── Bybit — borrow only ─────────────────────────────────────────────────────
class Bybit(Adapter):
    """spot-margin-trade/data publishes hourlyBorrowRate per VIP tier. The
    'No VIP' tier is the retail rate. No public supply-side rate exists."""

    name = "bybit"
    DATA = "https://api.bybit.com/v5/spot-margin-trade/data"
    TIER = "No VIP"

    async def snapshot(self, session):
        (d,), failures = await gather_isolated(session, [self.DATA])
        stats: dict = {}
        if d is None:
            return [], {"missing": 1}, failures
        tiers = (d.get("result") or {}).get("vipCoinList") or []
        tier = next((t for t in tiers if t.get("vipLevel") == self.TIER), None)
        if tier is None:
            stats["no_tier"] = 1
            log.error("[lending/bybit] tier %r absent (saw %s) — nothing written",
                      self.TIER, [t.get("vipLevel") for t in tiers][:6])
            return [], stats, failures
        rows = []
        for c in (tier.get("list") or []):
            if c.get("currency") not in ASSETS:
                continue
            r = Row(source=self.name, venue_kind=self.venue_kind,
                    asset=c["currency"], rate_type="borrow",
                    raw_rate=f(c.get("hourlyBorrowRate")), raw_basis="hourly",
                    rate_field="hourlyBorrowRate", tier=self.TIER, term=None,
                    endpoint=self.DATA,
                    extra=json.dumps({"borrowable": c.get("borrowable"),
                                      "maxBorrowingAmount": c.get("maxBorrowingAmount"),
                                      "collateralRatio": c.get("collateralRatio")}))
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


# ── KuCoin — supply only ────────────────────────────────────────────────────
class KuCoin(Adapter):
    """project/marketInterestRate is the clearing rate of KuCoin's lending
    market — what a lender earns, i.e. the SUPPLY side. It returns 168 hourly
    samples; only the newest is an observation of 'now'. DAILY basis.
    KuCoin's borrow rate needs KC-API-KEY and is therefore not collected."""

    name = "kucoin"
    RATE = "https://api.kucoin.com/api/v3/project/marketInterestRate?currency={}"

    async def snapshot(self, session):
        urls = [self.RATE.format(a) for a in ASSETS]
        res, failures = await gather_isolated(session, urls)
        stats: dict = {}
        rows = []
        for a, d in zip(ASSETS, res):
            if d is None:
                stats["missing"] = stats.get("missing", 0) + 1
                continue
            series = d.get("data") or []
            if not series:
                stats["missing"] = stats.get("missing", 0) + 1
                continue
            last = series[-1]
            r = Row(source=self.name, venue_kind=self.venue_kind, asset=a,
                    rate_type="supply", raw_rate=f(last.get("marketInterestRate")),
                    raw_basis="daily", rate_field="marketInterestRate",
                    tier="lending-market", term="flexible",
                    endpoint=self.RATE.format(a),
                    observed_at=ts_from_compact(last.get("time") or ""),
                    extra=json.dumps({"samples_returned": len(series)}))
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


# ── Binance — borrow only ───────────────────────────────────────────────────
class Binance(Adapter):
    """Binance's DOCUMENTED margin rate endpoints (sapi/v1/margin/
    interestRateHistory, crossMarginData) both require an API key, so the rate
    here comes from the public web endpoint the margin fee page itself calls.

    That endpoint is UNDOCUMENTED and may change or vanish without notice. It is
    used anyway because it is genuinely public and no-auth, and the cost of it
    breaking is bounded: per-source isolation means Binance rows simply stop and
    the health table records the failure. It is labelled undocumented in every
    row's `extra` so nobody later mistakes it for a supported API.

    dailyInterestRate, vipLevel '0' = retail.
    """

    name = "binance"
    SPEC = "https://www.binance.com/bapi/margin/v1/public/margin/vip/spec/list-all"
    TIER = "0"

    async def snapshot(self, session):
        (d,), failures = await gather_isolated(session, [self.SPEC])
        stats: dict = {}
        if d is None:
            return [], {"missing": 1}, failures
        rows = []
        for a in (d.get("data") or []):
            if a.get("assetName") not in ASSETS:
                continue
            spec = next((s for s in (a.get("specs") or [])
                         if s.get("vipLevel") == self.TIER), None)
            if spec is None:
                stats["no_tier"] = stats.get("no_tier", 0) + 1
                continue
            r = Row(source=self.name, venue_kind=self.venue_kind,
                    asset=a["assetName"], rate_type="borrow",
                    raw_rate=f(spec.get("dailyInterestRate")), raw_basis="daily",
                    rate_field="dailyInterestRate", tier=f"vip{self.TIER}",
                    term=None, endpoint=self.SPEC,
                    extra=json.dumps({"borrowLimit": spec.get("borrowLimit"),
                                      "endpoint_status": "undocumented-public"}))
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


# ── Aave via DefiLlama — supply + borrow ────────────────────────────────────
class Aave(Adapter):
    """The on-chain reference leg: Aave v3/v4 USDC and USDT on Ethereum.

    Sourced through the DefiLlama yields API rather than an RPC node, because it
    needs no node, no key and no ABI handling, and it already publishes both
    sides as ANNUAL PERCENT. /pools carries apyBase (supply); /lendBorrow
    carries apyBaseBorrow, joined on the pool id.

    apyBase is the pure interest rate; token incentives (apyReward) are recorded
    in `extra` but deliberately NOT folded into annual_pct — a reward APY paid
    in a volatile token is not the same asset as the rate we are comparing.

    Two calls totalling ~2.3 MB, so this runs on a SLOWER cadence than the CEX
    sources. Aave rates move per block but the daily shape is what matters for a
    cross-source correlation.
    """

    name = "aave"
    venue_kind = "defi"
    cadence_secs = 1800.0
    POOLS = "https://yields.llama.fi/pools"
    LEND = "https://yields.llama.fi/lendBorrow"
    PROJECTS = ("aave-v3", "aave-v4")
    CHAIN = "Ethereum"

    async def snapshot(self, session):
        (pools, lend), failures = await gather_isolated(session,
                                                       [self.POOLS, self.LEND])
        stats: dict = {}
        if pools is None or lend is None:
            return [], {"missing": 1}, failures
        data = pools.get("data") if isinstance(pools, dict) else pools
        borrow = {b["pool"]: b for b in (lend or [])}

        # A pool is only a LENDING MARKET if it appears in /lendBorrow. Requiring
        # that does two things at once:
        #   1. it excludes Aave's "Umbrella" pools, which are the safety-module
        #      product and have no borrow side at all, and
        #   2. it guarantees the supply and borrow legs of a row PAIR come from
        #      the SAME pool. Taking supply from one market and borrow from
        #      another would manufacture a spread that nobody can trade — the
        #      same failure as marking spot and perp at different instants.
        cand = [p for p in (data or [])
                if p.get("project") in self.PROJECTS
                and p.get("chain") == self.CHAIN
                and p.get("symbol") in ASSETS
                and p["pool"] in borrow]

        # Rank on /lendBorrow's totalSupplyUsd, NOT on /pools' tvlUsd. Measured
        # 2026-08-24: tvlUsd reported $1,053,137 for the Aave v3 Ethereum USDC
        # core market that lendBorrow shows holding $2,013,794,844, while an
        # Umbrella pool reported $58.7M. Ranking on tvlUsd therefore selects the
        # wrong market by three orders of magnitude.
        best: dict = {}
        for p in cand:
            k = (p["project"], p["symbol"])
            sz = borrow[p["pool"]].get("totalSupplyUsd") or 0.0
            if k not in best or sz > (borrow[best[k]["pool"]].get("totalSupplyUsd") or 0.0):
                best[k] = p
        for proj in self.PROJECTS:
            if not any(k[0] == proj for k in best):
                stats["no_market"] = stats.get("no_market", 0) + 1
                log.info("[lending/aave] %s: no %s pool with a borrow side in "
                         "/lendBorrow — nothing written for it this cycle",
                         proj, self.CHAIN)

        rows = []
        for (proj, sym), p in sorted(best.items()):
            b = borrow[p["pool"]]
            sup_usd = b.get("totalSupplyUsd") or 0.0
            bor_usd = b.get("totalBorrowUsd") or 0.0
            # Utilisation is what explains an outlier: an Aave rate sits on a
            # kinked curve, so near-100% utilisation sends it far above the CEX
            # level. Without this a reader cannot tell a real yield from a
            # transient squeeze.
            util = (bor_usd / sup_usd) if sup_usd else None
            shared = {"pool": p.get("pool"), "poolMeta": p.get("poolMeta"),
                      "totalSupplyUsd": sup_usd, "totalBorrowUsd": bor_usd,
                      "utilisation": util, "ltv": b.get("ltv")}
            common = dict(source=proj, venue_kind=self.venue_kind, asset=sym,
                          tier=self.CHAIN, term="flexible")
            r = Row(**common, rate_type="supply", raw_rate=f(p.get("apyBase")),
                    raw_basis="annual_pct", rate_field="apyBase",
                    endpoint=self.POOLS,
                    extra=json.dumps({**shared,
                                      "apyReward": p.get("apyReward"),
                                      "pools_tvlUsd_unreliable": p.get("tvlUsd")}))
            if screen(r, stats):
                rows.append(r)
            r = Row(**common, rate_type="borrow",
                    raw_rate=f(b.get("apyBaseBorrow")),
                    raw_basis="annual_pct", rate_field="apyBaseBorrow",
                    endpoint=self.LEND,
                    extra=json.dumps({**shared,
                                      "apyRewardBorrow": b.get("apyRewardBorrow")}))
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


ADAPTERS = (OKX, Bybit, KuCoin, Binance, Aave)
