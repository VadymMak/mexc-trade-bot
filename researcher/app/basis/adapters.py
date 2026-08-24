"""Per-venue adapters for the dated-futures basis collector.

READ-ONLY PUBLIC ENDPOINTS ONLY. No keys, no orders.

Each adapter is fully ISOLATED: it owns its own endpoints, its own instrument
cache and its own failure counters. A venue that errors, times out or returns
nonsense removes only its own rows for that cycle. Within a venue the endpoints
are isolated from each other too, so a dead spot feed does not discard the
futures feed.

VENUES DELIBERATELY ABSENT (checked 2026-08-24, both reachable):
  bitget — /api/v2/mix/market/contracts returns 759 contracts, symbolType
           'perpetual' for all 759 and deliveryPeriod empty for all 759.
           Bitget lists NO dated futures, so there is nothing to collect.
  kucoin — /api/v1/contracts/active exposes exactly ONE dated contract
           (XBTMU26, type FFICSX, isInverse, multiplier -1.0). The other two
           rows carrying an expireDate are perpetuals being delisted. A
           bespoke negative-multiplier unit path for a single contract is more
           unit risk than the one row is worth; revisit if KuCoin lists a real
           dated curve.
Both are recorded here rather than silently omitted, so the next reader knows
they were tested rather than forgotten.
"""
from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from .common import (Row, f, gather_isolated, get_json, ms_to_dt, pos, px,
                     s_to_dt, screen)

log = logging.getLogger("basis")

# Instrument definitions change on listing/expiry, not per tick.
INSTRUMENT_TTL_SECS = 3600.0


class Adapter:
    """Base: instrument cache + refresh cadence. Subclasses own the fetching."""

    name = "?"

    def __init__(self) -> None:
        self.universe: dict = {}          # future_symbol -> instrument meta
        self._refreshed_at: float | None = None

    def due(self, t0: float) -> bool:
        return (self._refreshed_at is None
                or t0 - self._refreshed_at >= INSTRUMENT_TTL_SECS)

    async def refresh(self, session: aiohttp.ClientSession, t0: float) -> None:
        uni = await self._load_instruments(session)
        if uni:                            # keep the last good map on a bad pull
            self.universe = uni
            self._refreshed_at = t0
            log.info("[basis/%s] instruments refreshed: %d dated contract(s)",
                     self.name, len(uni))
        else:
            log.error("[basis/%s] instrument refresh returned NOTHING — keeping "
                      "previous map of %d", self.name, len(self.universe))

    async def _load_instruments(self, session) -> dict:
        raise NotImplementedError

    async def snapshot(self, session) -> tuple[list[Row], dict, list]:
        raise NotImplementedError


# ── OKX ─────────────────────────────────────────────────────────────────────
class OKX(Adapter):
    """OKX dated futures. instType=FUTURES also carries 'XPERP' instruments —
    nominal 5-year expiries that are perps in all but name. They are excluded
    on THREE independent signals so a rename on OKX's side cannot let them in."""

    name = "okx"
    INSTR = "https://www.okx.com/api/v5/public/instruments?instType=FUTURES"
    FUT   = "https://www.okx.com/api/v5/market/tickers?instType=FUTURES"
    SPOT  = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"

    @staticmethod
    def _is_xperp(i: dict) -> bool:
        return (i.get("ruleType") == "xperp"
                or "XPERP" in (i.get("instId") or "")
                or i.get("alias") == "this_five_years")

    async def _load_instruments(self, session) -> dict:
        d = (await get_json(session, self.INSTR)).get("data") or []
        out = {}
        for i in d:
            if self._is_xperp(i) or i.get("state") != "live":
                continue
            exp = ms_to_dt(i.get("expTime"))
            if exp is None:
                continue
            iid = i["instId"]
            ctval, ctmult = f(i.get("ctVal")), f(i.get("ctMult"))
            out[iid] = {
                "coin": iid.split("-")[0],
                "expiry": exp,
                "ctype": i.get("ctType") or None,
                "settle": i.get("settleCcy") or None,
                "cycle": i.get("alias") or None,
                "mult": (ctval * ctmult) if (ctval and ctmult) else None,
            }
        return out

    async def snapshot(self, session):
        (fut, spot), failures = await gather_isolated(session, [self.FUT, self.SPOT])
        stats: dict = {}
        if fut is None or spot is None:
            return [], stats, failures
        ft = {x["instId"]: x for x in (fut.get("data") or [])}
        st = {x["instId"]: x for x in (spot.get("data") or [])}
        rows = []
        for sym, m in self.universe.items():
            t = ft.get(sym)
            if t is None:
                stats["unpriced"] = stats.get("unpriced", 0) + 1
                continue
            # An inverse contract quotes in USD; the closest OKX spot book is
            # the USDT pair, so the leg is a PROXY and is labelled as one.
            sp_sym = f"{m['coin']}-USDT"
            s = st.get(sp_sym)
            if s is None:
                sp_sym = f"{m['coin']}-USDC"
                s = st.get(sp_sym)
            if s is None:
                stats["no_spot"] = stats.get("no_spot", 0) + 1
                continue
            fb, fa = pos(t.get("bidPx")), pos(t.get("askPx"))
            sb, sa = pos(s.get("bidPx")), pos(s.get("askPx"))
            sp, sps = px(sb, sa, ("last", s.get("last")))
            fp, fps = px(fb, fa, ("last", t.get("last")))
            r = Row(exchange=self.name, coin=m["coin"], future_symbol=sym,
                    spot_symbol=sp_sym, contract_type=m["ctype"],
                    settle_ccy=m["settle"], cycle_label=m["cycle"],
                    expiry_ts=m["expiry"], expiry_source="okx.instruments.expTime",
                    spot_price=sp, spot_bid=sb, spot_ask=sa, spot_px_source=sps,
                    spot_source=("okx_spot" if m["ctype"] == "linear"
                                 else "okx_spot_usdt_proxy"),
                    future_price=fp, future_bid=fb, future_ask=fa,
                    future_px_source=fps,
                    future_vol24_usd=f(t.get("volCcy24h")),
                    spot_vol24_usd=f(s.get("volCcy24h")),
                    contract_multiplier=m["mult"])
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


# ── Bybit ───────────────────────────────────────────────────────────────────
class Bybit(Adapter):
    """Bybit dated futures across both margin modes. A dated contract is one
    whose deliveryTime is a real timestamp; perps report '0'."""

    name = "bybit"
    CATEGORIES = ("linear", "inverse")
    INSTR = "https://api.bybit.com/v5/market/instruments-info?category={}&limit=1000"
    TICK  = "https://api.bybit.com/v5/market/tickers?category={}"
    SPOT  = "https://api.bybit.com/v5/market/tickers?category=spot"

    async def _load_instruments(self, session) -> dict:
        urls = [self.INSTR.format(c) for c in self.CATEGORIES]
        res, failures = await gather_isolated(session, urls)
        for fl in failures:
            log.warning("[basis/bybit] instrument feed down: %s", fl.url)
        out = {}
        for cat, d in zip(self.CATEGORIES, res):
            if d is None:
                continue
            for i in (d.get("result", {}).get("list") or []):
                dt = ms_to_dt(i.get("deliveryTime"))
                if dt is None or i.get("status") not in (None, "Trading"):
                    continue
                out[i["symbol"]] = {
                    "coin": i.get("baseCoin"),
                    "quote": i.get("quoteCoin"),
                    "expiry": dt,
                    "ctype": "linear" if cat == "linear" else "inverse",
                    "settle": i.get("settleCoin"),
                    "cycle": i.get("contractType"),
                    "cat": cat,
                }
        return out

    async def snapshot(self, session):
        urls = [self.TICK.format(c) for c in self.CATEGORIES] + [self.SPOT]
        res, failures = await gather_isolated(session, urls)
        stats: dict = {}
        ft: dict = {}
        for d in res[:-1]:
            if d is None:
                continue
            for x in (d.get("result", {}).get("list") or []):
                ft[x["symbol"]] = x
        spot = res[-1]
        if spot is None or not ft:
            return [], stats, failures
        st = {x["symbol"]: x for x in (spot.get("result", {}).get("list") or [])}
        rows = []
        for sym, m in self.universe.items():
            t = ft.get(sym)
            if t is None:
                stats["unpriced"] = stats.get("unpriced", 0) + 1
                continue
            sp_sym = f"{m['coin']}USDT"
            s = st.get(sp_sym)
            if s is None:
                stats["no_spot"] = stats.get("no_spot", 0) + 1
                continue
            fb, fa = pos(t.get("bid1Price")), pos(t.get("ask1Price"))
            sb, sa = pos(s.get("bid1Price")), pos(s.get("ask1Price"))
            sp, sps = px(sb, sa, ("last", s.get("lastPrice")))
            fp, fps = px(fb, fa, ("mark", t.get("markPrice")),
                         ("last", t.get("lastPrice")))
            r = Row(exchange=self.name, coin=m["coin"], future_symbol=sym,
                    spot_symbol=sp_sym, contract_type=m["ctype"],
                    settle_ccy=m["settle"], cycle_label=m["cycle"],
                    expiry_ts=m["expiry"],
                    expiry_source="bybit.instruments-info.deliveryTime",
                    spot_price=sp, spot_bid=sb, spot_ask=sa, spot_px_source=sps,
                    spot_source=("bybit_spot" if m["ctype"] == "linear"
                                 else "bybit_spot_usdt_proxy"),
                    future_price=fp, future_bid=fb, future_ask=fa,
                    future_px_source=fps, future_mark=pos(t.get("markPrice")),
                    venue_basis_raw=f(t.get("basisRateYear")),
                    venue_basis_field="basisRateYear",
                    future_oi=f(t.get("openInterestValue")),
                    future_vol24_usd=f(t.get("turnover24h")),
                    spot_vol24_usd=f(s.get("turnover24h")))
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


# ── Gate (delivery) ─────────────────────────────────────────────────────────
class GateDelivery(Adapter):
    """Gate's USDT-settled DELIVERY market. This is a different endpoint family
    from the /futures/usdt perp feed the carry collector uses; nothing here
    reads or writes any carry table."""

    name = "gate"
    CONTRACTS = "https://api.gateio.ws/api/v4/delivery/usdt/contracts"
    TICKERS   = "https://api.gateio.ws/api/v4/delivery/usdt/tickers"
    SPOT      = "https://api.gateio.ws/api/v4/spot/tickers"

    async def _load_instruments(self, session) -> dict:
        d = await get_json(session, self.CONTRACTS)
        out = {}
        for i in d or []:
            if i.get("in_delisting"):
                continue
            exp = s_to_dt(i.get("expire_time"))
            if exp is None:
                continue
            uly = i.get("underlying") or ""
            out[i["name"]] = {
                "coin": uly.split("_")[0] or None,
                "spot": uly or None,
                "expiry": exp,
                "ctype": "linear",
                "settle": "USDT",
                "cycle": i.get("cycle"),
                "mult": f(i.get("quanto_multiplier")),
            }
        return out

    async def snapshot(self, session):
        (tick, spot), failures = await gather_isolated(
            session, [self.TICKERS, self.SPOT])
        stats: dict = {}
        if tick is None or spot is None:
            return [], stats, failures
        ft = {x["contract"]: x for x in (tick or [])}
        st = {x["currency_pair"]: x for x in (spot or [])}
        rows = []
        for sym, m in self.universe.items():
            t = ft.get(sym)
            if t is None:
                stats["unpriced"] = stats.get("unpriced", 0) + 1
                continue
            s = st.get(m["spot"])
            if s is None:
                stats["no_spot"] = stats.get("no_spot", 0) + 1
                continue
            fb, fa = pos(t.get("highest_bid")), pos(t.get("lowest_ask"))
            sb, sa = pos(s.get("highest_bid")), pos(s.get("lowest_ask"))
            sp, sps = px(sb, sa, ("last", s.get("last")))
            fp, fps = px(fb, fa, ("mark", t.get("mark_price")),
                         ("last", t.get("last")))
            r = Row(exchange=self.name, coin=m["coin"], future_symbol=sym,
                    spot_symbol=m["spot"], contract_type=m["ctype"],
                    settle_ccy=m["settle"], cycle_label=m["cycle"],
                    expiry_ts=m["expiry"],
                    expiry_source="gate.delivery.contracts.expire_time",
                    spot_price=sp, spot_bid=sb, spot_ask=sa, spot_px_source=sps,
                    spot_source="gate_spot",
                    future_price=fp, future_bid=fb, future_ask=fa,
                    future_px_source=fps, future_mark=pos(t.get("mark_price")),
                    venue_basis_raw=f(t.get("basis_rate")),
                    venue_basis_field="basis_rate",
                    future_oi=f(t.get("total_size")),
                    future_vol24_usd=f(t.get("volume_24h_quote")),
                    spot_vol24_usd=f(s.get("quote_volume")),
                    contract_multiplier=m["mult"])
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


# ── Deribit ─────────────────────────────────────────────────────────────────
class Deribit(Adapter):
    """Deribit dated futures (BTC/ETH). Inverse, USD-quoted, settled in coin.

    Deribit has NO spot market, so the spot leg is its own composite index
    (labelled `deribit_index_*`). That index has no book, so spot_bid/ask and
    therefore roundtrip_spread_bps are NULL for every Deribit row BY
    CONSTRUCTION — the real spot leg of this trade would be executed on another
    venue. Left NULL rather than filled with the future's own book, which would
    understate the cost of the trade.
    """

    name = "deribit"
    CURRENCIES = ("BTC", "ETH")
    INSTR = "https://www.deribit.com/api/v2/public/get_instruments?currency={}&kind=future"
    BOOK  = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={}&kind=future"
    INDEX = "https://www.deribit.com/api/v2/public/get_index_price?index_name={}_usd"

    async def _load_instruments(self, session) -> dict:
        res, failures = await gather_isolated(
            session, [self.INSTR.format(c) for c in self.CURRENCIES])
        for fl in failures:
            log.warning("[basis/deribit] instrument feed down: %s", fl.url)
        out = {}
        for cur, d in zip(self.CURRENCIES, res):
            if d is None:
                continue
            for i in (d.get("result") or []):
                if i.get("settlement_period") == "perpetual":
                    continue
                exp = ms_to_dt(i.get("expiration_timestamp"))
                if exp is None or not i.get("is_active", True):
                    continue
                out[i["instrument_name"]] = {
                    "coin": i.get("base_currency") or cur,
                    "cur": cur,
                    "expiry": exp,
                    "ctype": "inverse",
                    "settle": i.get("settlement_currency") or cur,
                    "cycle": i.get("settlement_period"),
                    "mult": f(i.get("contract_size")),
                }
        return out

    async def snapshot(self, session):
        urls = ([self.BOOK.format(c) for c in self.CURRENCIES]
                + [self.INDEX.format(c.lower()) for c in self.CURRENCIES])
        res, failures = await gather_isolated(session, urls)
        stats: dict = {}
        n = len(self.CURRENCIES)
        books: dict = {}
        for d in res[:n]:
            if d is None:
                continue
            for x in (d.get("result") or []):
                books[x["instrument_name"]] = x
        index = {}
        for cur, d in zip(self.CURRENCIES, res[n:]):
            if d is not None:
                index[cur] = pos((d.get("result") or {}).get("index_price"))
        if not books:
            return [], stats, failures
        rows = []
        for sym, m in self.universe.items():
            t = books.get(sym)
            if t is None:
                stats["unpriced"] = stats.get("unpriced", 0) + 1
                continue
            spx = index.get(m["cur"])
            if spx is None:
                stats["no_spot"] = stats.get("no_spot", 0) + 1
                continue
            fb, fa = pos(t.get("bid_price")), pos(t.get("ask_price"))
            fp, fps = px(fb, fa, ("mark", t.get("mark_price")),
                         ("last", t.get("last")))
            r = Row(exchange=self.name, coin=m["coin"], future_symbol=sym,
                    spot_symbol=f"{m['cur'].lower()}_usd", contract_type=m["ctype"],
                    settle_ccy=m["settle"], cycle_label=m["cycle"],
                    expiry_ts=m["expiry"],
                    expiry_source="deribit.get_instruments.expiration_timestamp",
                    spot_price=spx, spot_bid=None, spot_ask=None,
                    spot_px_source="index",
                    spot_source=f"deribit_index_{m['cur'].lower()}_usd",
                    future_price=fp, future_bid=fb, future_ask=fa,
                    future_px_source=fps, future_mark=pos(t.get("mark_price")),
                    venue_basis_raw=f(t.get("estimated_delivery_price")),
                    venue_basis_field="estimated_delivery_price",
                    future_oi=f(t.get("open_interest")),
                    future_vol24_usd=f(t.get("volume_usd")),
                    contract_multiplier=m["mult"])
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


# ── Binance (COIN-M delivery) ───────────────────────────────────────────────
class BinanceDelivery(Adapter):
    """Binance COIN-margined quarterlies (dapi). Inverse, USD-quoted, settled in
    the base coin, so the USDT spot book is a PROXY for the spot leg."""

    name = "binance"
    INFO = "https://dapi.binance.com/dapi/v1/exchangeInfo"
    TICK = "https://dapi.binance.com/dapi/v1/ticker/bookTicker"
    SPOT = "https://api.binance.com/api/v3/ticker/bookTicker"
    DATED = ("CURRENT_QUARTER", "NEXT_QUARTER")

    async def _load_instruments(self, session) -> dict:
        d = await get_json(session, self.INFO)
        out = {}
        for i in (d.get("symbols") or []):
            if i.get("contractType") not in self.DATED:
                continue
            if i.get("contractStatus", i.get("status")) != "TRADING":
                continue
            exp = ms_to_dt(i.get("deliveryDate"))
            if exp is None:
                continue
            out[i["symbol"]] = {
                "coin": i.get("baseAsset"),
                "expiry": exp,
                "ctype": "inverse",
                "settle": i.get("marginAsset"),
                "cycle": i.get("contractType"),
                "mult": f(i.get("contractSize")),
            }
        return out

    async def snapshot(self, session):
        (tick, spot), failures = await gather_isolated(session, [self.TICK, self.SPOT])
        stats: dict = {}
        if tick is None or spot is None:
            return [], stats, failures
        ft = {x["symbol"]: x for x in (tick or [])}
        st = {x["symbol"]: x for x in (spot or [])}
        rows = []
        for sym, m in self.universe.items():
            t = ft.get(sym)
            if t is None:
                stats["unpriced"] = stats.get("unpriced", 0) + 1
                continue
            sp_sym = f"{m['coin']}USDT"
            s = st.get(sp_sym)
            if s is None:
                stats["no_spot"] = stats.get("no_spot", 0) + 1
                continue
            fb, fa = pos(t.get("bidPrice")), pos(t.get("askPrice"))
            sb, sa = pos(s.get("bidPrice")), pos(s.get("askPrice"))
            sp, sps = px(sb, sa)
            fp, fps = px(fb, fa)
            r = Row(exchange=self.name, coin=m["coin"], future_symbol=sym,
                    spot_symbol=sp_sym, contract_type=m["ctype"],
                    settle_ccy=m["settle"], cycle_label=m["cycle"],
                    expiry_ts=m["expiry"],
                    expiry_source="binance.dapi.exchangeInfo.deliveryDate",
                    spot_price=sp, spot_bid=sb, spot_ask=sa, spot_px_source=sps,
                    spot_source="binance_spot_usdt_proxy",
                    future_price=fp, future_bid=fb, future_ask=fa,
                    future_px_source=fps,
                    contract_multiplier=m["mult"])
            if screen(r, stats):
                rows.append(r)
        return rows, stats, failures


ADAPTERS = (OKX, Bybit, GateDelivery, Deribit, BinanceDelivery)
