"""Per-venue adapters: OKX, Bitget, KuCoin, Bybit.

Each adapter owns exactly one venue's quirks and hands back canonical `Row`s.
The quirks are the whole reason this file exists — the four venues disagree on
symbol format, on funding-interval discovery, and above all on CONTRACT UNITS:

    Bybit   linear USDT perps  -> qty is BASE COIN, no multiplier
    Bitget  USDT-M perps       -> qty is BASE COIN, no multiplier
                                  (`sizeMultiplier` is the LOT STEP, not a
                                   contract size — reading it as a multiplier
                                   would scale every size by up to 1000x)
    OKX     linear swaps       -> qty is CONTRACTS; 1 contract = ctVal x ctMult
                                  of ctValCcy (the base coin)
    KuCoin  USDT-M futures     -> qty is CONTRACTS; 1 contract = `multiplier`
                                  base units (0.01 … 1000 across the universe)

`contract_multiplier` is written ONLY where the venue publishes it and we
therefore measured it (OKX, KuCoin). For Bybit/Bitget it is NULL, not 1.0 —
storing 1.0 would assert a convention we did not verify, and a NULL that means
"not applicable" is honest where a 1.0 that means "we assumed" is not.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .common import Row, f, get_json, gather_isolated, mid, unit_gate

log = logging.getLogger("venues")


class Adapter:
    """Base: a TTL'd universe refresh plus a per-cycle snapshot."""

    name = "base"
    refresh_hours = 6.0

    def __init__(self) -> None:
        self._last_refresh = -1e9
        self.universe: dict = {}

    def due(self, now_mono: float) -> bool:
        return (now_mono - self._last_refresh) >= self.refresh_hours * 3600.0

    async def refresh(self, session, now_mono: float) -> int:
        raise NotImplementedError

    async def snapshot(self, session) -> tuple[list[Row], dict, list]:
        raise NotImplementedError


# ─────────────────────────────── OKX ────────────────────────────────────────
class OkxAdapter(Adapter):
    """OKX v5. Funding interval is MEASURED as nextFundingTime - fundingTime.

    The funding endpoint accepts instId=ANY, which returns every swap in one
    call — no per-symbol fan-out over ~215 names.
    """

    name = "okx"
    B = "https://www.okx.com/api/v5/"
    I_SWAP = B + "public/instruments?instType=SWAP"
    I_SPOT = B + "public/instruments?instType=SPOT"
    T_SWAP = B + "market/tickers?instType=SWAP"
    T_SPOT = B + "market/tickers?instType=SPOT"
    MARK = B + "public/mark-price?instType=SWAP"
    FUND = B + "public/funding-rate?instId=ANY"

    async def refresh(self, session, now_mono: float) -> int:
        try:
            sw = await get_json(session, self.I_SWAP)
            sp = await get_json(session, self.I_SPOT)
        except Exception as exc:                      # noqa: BLE001
            log.warning("[venues/okx] instrument refresh failed: %r (keeping %d)",
                        exc, len(self.universe))
            return 0
        spot_ids = {i["instId"] for i in sp.get("data", []) if i.get("state") == "live"}
        uni = {}
        for i in sw.get("data", []):
            if (i.get("settleCcy") != "USDT" or i.get("state") != "live"
                    or i.get("ctType") != "linear"):
                continue
            spot_id = i["instId"][:-5]                # BTC-USDT-SWAP -> BTC-USDT
            if spot_id not in spot_ids:
                continue
            ct_val, ct_mult = f(i.get("ctVal")), f(i.get("ctMult"))
            if not ct_val or not ct_mult:
                continue                              # unmeasured units => skip
            uni[i["instId"]] = {"spot": spot_id, "mult": ct_val * ct_mult}
        if uni:
            self.universe = uni
            self._last_refresh = now_mono
            log.info("[venues/okx] universe: %d dual-listed USDT swaps", len(uni))
        return len(uni)

    async def snapshot(self, session) -> tuple[list[Row], dict, list]:
        res, failures = await gather_isolated(
            session, [self.T_SWAP, self.T_SPOT, self.MARK, self.FUND])
        tsw, tsp, mk, fr = res
        stats = {"unpriced": 0, "unit_skip": 0, "no_interval": 0}
        if tsw is None or tsp is None:
            return [], stats, failures

        pt = {x["instId"]: x for x in tsw.get("data", [])}
        st = {x["instId"]: x for x in tsp.get("data", [])}
        mp = {x["instId"]: x for x in (mk or {}).get("data", [])}
        fd = {x["instId"]: x for x in (fr or {}).get("data", [])}

        rows: list[Row] = []
        for inst, meta in self.universe.items():
            p, s = pt.get(inst), st.get(meta["spot"])
            if p is None or s is None:
                continue
            perp_bid, perp_ask = f(p.get("bidPx")), f(p.get("askPx"))
            spot_bid, spot_ask = f(s.get("bidPx")), f(s.get("askPx"))
            spot_price = mid(spot_bid, spot_ask)
            perp_mark = f((mp.get(inst) or {}).get("markPx")) or mid(perp_bid, perp_ask)
            if perp_mark is None or spot_price is None:
                stats["unpriced"] += 1
                continue
            if not unit_gate(perp_mark, spot_price):
                stats["unit_skip"] += 1
                continue

            # Interval MEASURED from the venue's own two timestamps.
            # OKX names these confusingly: `fundingTime` is the UPCOMING
            # settlement and `nextFundingTime` is the one AFTER it. So the
            # interval is their difference, but the next settlement is
            # `fundingTime`. Using nextFundingTime as "next" put OKX a full
            # period ahead of every other venue (672 min vs 192 on BTC).
            d = fd.get(inst) or {}
            ft, nft = f(d.get("fundingTime")), f(d.get("nextFundingTime"))
            iv = (nft - ft) / 3600000.0 if ft and nft and nft > ft else None
            if iv is None:
                stats["no_interval"] += 1
                continue
            nxt = datetime.fromtimestamp(ft / 1000.0, timezone.utc) if ft else None

            m = meta["mult"]
            vol_c = f(p.get("vol24h"))
            rows.append(Row(
                exchange="okx", symbol=inst, perp_mark=perp_mark,
                spot_price=spot_price, funding_rate=f(d.get("fundingRate")),
                interval_h=iv, next_settle=nxt,
                interval_source="okx.nextFundingTime-fundingTime",
                perp_bid=perp_bid, perp_ask=perp_ask,
                spot_bid=spot_bid, spot_ask=spot_ask,
                # contracts -> base units via the measured contract size
                perp_vol_base=(vol_c * m) if vol_c is not None else None,
                perp_vol_usd=(vol_c * m * perp_mark) if vol_c is not None else None,
                perp_oi=None,
                spot_vol_base=f(s.get("vol24h")), spot_vol_usd=f(s.get("volCcy24h")),
                perp_bid_size=(f(p.get("bidSz")) or 0) * m if f(p.get("bidSz")) is not None else None,
                perp_ask_size=(f(p.get("askSz")) or 0) * m if f(p.get("askSz")) is not None else None,
                spot_bid_size=f(s.get("bidSz")), spot_ask_size=f(s.get("askSz")),
                contract_multiplier=m,
                perp_index_price=f((mp.get(inst) or {}).get("idxPx")),
            ))
        return rows, stats, failures


# ────────────────────────────── Bitget ──────────────────────────────────────
class BitgetAdapter(Adapter):
    """Bitget v2 USDT-FUTURES. `fundInterval` is published in HOURS per symbol.

    Bitget's mix ticker carries no next-settlement timestamp, so next_settle /
    mins_to_funding are left NULL rather than derived from an assumed UTC
    boundary alignment. The interval — the thing APR actually depends on — is
    measured.
    """

    name = "bitget"
    B = "https://api.bitget.com/api/v2/"
    CONTRACTS = B + "mix/market/contracts?productType=USDT-FUTURES"
    T_PERP = B + "mix/market/tickers?productType=USDT-FUTURES"
    T_SPOT = B + "spot/market/tickers"

    async def refresh(self, session, now_mono: float) -> int:
        try:
            c = await get_json(session, self.CONTRACTS)
            s = await get_json(session, self.T_SPOT)
        except Exception as exc:                      # noqa: BLE001
            log.warning("[venues/bitget] refresh failed: %r (keeping %d)",
                        exc, len(self.universe))
            return 0
        spot = {x["symbol"] for x in s.get("data", [])}
        uni = {}
        for x in c.get("data", []):
            if (x.get("symbolType") != "perpetual" or x.get("symbolStatus") != "normal"
                    or x.get("quoteCoin") != "USDT" or x["symbol"] not in spot):
                continue
            iv = f(x.get("fundInterval"))
            if not iv:
                continue                              # unresolved => not collected
            uni[x["symbol"]] = {"iv": iv}
        if uni:
            self.universe = uni
            self._last_refresh = now_mono
            log.info("[venues/bitget] universe: %d dual-listed USDT perps", len(uni))
        return len(uni)

    async def snapshot(self, session) -> tuple[list[Row], dict, list]:
        res, failures = await gather_isolated(session, [self.T_PERP, self.T_SPOT])
        tp, ts = res
        stats = {"unpriced": 0, "unit_skip": 0, "no_interval": 0}
        if tp is None or ts is None:
            return [], stats, failures
        pt = {x["symbol"]: x for x in tp.get("data", [])}
        st = {x["symbol"]: x for x in ts.get("data", [])}

        rows: list[Row] = []
        for sym, meta in self.universe.items():
            p, s = pt.get(sym), st.get(sym)
            if p is None or s is None:
                continue
            perp_bid, perp_ask = f(p.get("bidPr")), f(p.get("askPr"))
            spot_bid, spot_ask = f(s.get("bidPr")), f(s.get("askPr"))
            spot_price = mid(spot_bid, spot_ask)
            perp_mark = f(p.get("markPrice")) or mid(perp_bid, perp_ask)
            if perp_mark is None or spot_price is None:
                stats["unpriced"] += 1
                continue
            if not unit_gate(perp_mark, spot_price):
                stats["unit_skip"] += 1
                continue
            rows.append(Row(
                exchange="bitget", symbol=sym, perp_mark=perp_mark,
                spot_price=spot_price, funding_rate=f(p.get("fundingRate")),
                interval_h=meta["iv"], next_settle=None,
                interval_source="bitget.contracts.fundInterval",
                perp_bid=perp_bid, perp_ask=perp_ask,
                spot_bid=spot_bid, spot_ask=spot_ask,
                # qty is base coin: sizeMultiplier is the LOT STEP, not a
                # contract size, so it must NOT be applied here.
                perp_vol_base=f(p.get("baseVolume")), perp_vol_usd=f(p.get("usdtVolume")),
                perp_oi=f(p.get("holdingAmount")),
                spot_vol_base=f(s.get("baseVolume")), spot_vol_usd=f(s.get("usdtVolume")),
                perp_bid_size=f(p.get("bidSz")), perp_ask_size=f(p.get("askSz")),
                spot_bid_size=f(s.get("bidSz")), spot_ask_size=f(s.get("askSz")),
                contract_multiplier=None,             # not applicable, not assumed
                perp_index_price=f(p.get("indexPrice")),
            ))
        return rows, stats, failures


# ────────────────────────────── KuCoin ──────────────────────────────────────
class KucoinAdapter(Adapter):
    """KuCoin: futures live on api-futures.kucoin.com, spot on api.kucoin.com.

    Two quirks beyond the split hosts:
      * futures call BTC "XBT" (XBTUSDTM), so the base currency needs aliasing
        before it will match the spot symbol BTC-USDT;
      * `nextFundingRateTime` is a COUNTDOWN in milliseconds, not a timestamp.
        Reading it as an epoch would place settlement in 1970.
    """

    name = "kucoin"
    CONTRACTS = "https://api-futures.kucoin.com/api/v1/contracts/active"
    T_PERP = "https://api-futures.kucoin.com/api/v1/allTickers"
    T_SPOT = "https://api.kucoin.com/api/v1/market/allTickers"
    ALIAS = {"XBT": "BTC"}

    def _spot_symbol(self, c: dict) -> str:
        base = self.ALIAS.get(c.get("baseCurrency"), c.get("baseCurrency"))
        return f"{base}-{c.get('quoteCurrency')}"

    async def refresh(self, session, now_mono: float) -> int:
        try:
            c = await get_json(session, self.CONTRACTS)
            s = await get_json(session, self.T_SPOT)
        except Exception as exc:                      # noqa: BLE001
            log.warning("[venues/kucoin] refresh failed: %r (keeping %d)",
                        exc, len(self.universe))
            return 0
        spot = {t["symbol"] for t in s.get("data", {}).get("ticker", [])}
        uni = {}
        for x in c.get("data", []):
            if (x.get("settleCurrency") != "USDT" or x.get("status") != "Open"
                    or x.get("isInverse")):
                continue
            sp = self._spot_symbol(x)
            if sp not in spot:
                continue
            m = f(x.get("multiplier"))
            gran = f(x.get("fundingRateGranularity"))
            if not m or not gran:
                continue                              # unmeasured => skip
            uni[x["symbol"]] = {"spot": sp, "mult": m, "iv": gran / 3600000.0}
        if uni:
            self.universe = uni
            self._last_refresh = now_mono
            log.info("[venues/kucoin] universe: %d dual-listed USDT futures", len(uni))
        return len(uni)

    async def snapshot(self, session) -> tuple[list[Row], dict, list]:
        res, failures = await gather_isolated(
            session, [self.CONTRACTS, self.T_PERP, self.T_SPOT])
        cts, tp, ts = res
        stats = {"unpriced": 0, "unit_skip": 0, "no_interval": 0}
        if cts is None or tp is None or ts is None:
            return [], stats, failures
        ct = {x["symbol"]: x for x in cts.get("data", [])}
        pt = {x["symbol"]: x for x in tp.get("data", [])}
        st = {x["symbol"]: x for x in ts.get("data", {}).get("ticker", [])}

        rows: list[Row] = []
        now = datetime.now(timezone.utc)
        for sym, meta in self.universe.items():
            c, p, s = ct.get(sym), pt.get(sym), st.get(meta["spot"])
            if c is None or p is None or s is None:
                continue
            perp_bid, perp_ask = f(p.get("bestBidPrice")), f(p.get("bestAskPrice"))
            spot_bid, spot_ask = f(s.get("buy")), f(s.get("sell"))
            spot_price = mid(spot_bid, spot_ask)
            perp_mark = f(c.get("markPrice")) or mid(perp_bid, perp_ask)
            if perp_mark is None or spot_price is None:
                stats["unpriced"] += 1
                continue
            if not unit_gate(perp_mark, spot_price):
                stats["unit_skip"] += 1
                continue

            # COUNTDOWN in ms, not an epoch.
            cd = f(c.get("nextFundingRateTime"))
            nxt = now + timedelta(milliseconds=cd) if cd and cd > 0 else None
            m = meta["mult"]
            bs, as_ = f(p.get("bestBidSize")), f(p.get("bestAskSize"))
            rows.append(Row(
                exchange="kucoin", symbol=sym, perp_mark=perp_mark,
                spot_price=spot_price, funding_rate=f(c.get("fundingFeeRate")),
                interval_h=meta["iv"], next_settle=nxt,
                interval_source="kucoin.fundingRateGranularity",
                perp_bid=perp_bid, perp_ask=perp_ask,
                spot_bid=spot_bid, spot_ask=spot_ask,
                perp_vol_base=None, perp_vol_usd=None, perp_oi=None,
                spot_vol_base=f(s.get("vol")), spot_vol_usd=f(s.get("volValue")),
                # contracts -> base units via the measured multiplier
                perp_bid_size=(bs * m) if bs is not None else None,
                perp_ask_size=(as_ * m) if as_ is not None else None,
                spot_bid_size=f(s.get("bestBidSize")),
                spot_ask_size=f(s.get("bestAskSize")),
                contract_multiplier=m,
                perp_index_price=f(c.get("indexPrice")),
            ))
        return rows, stats, failures


# ────────────────────────────── Bybit ───────────────────────────────────────
class BybitAdapter(Adapter):
    """Bybit v5 — mirrors app/bybit/main.py so all four venues land in one table.

    The standalone mexc-bybit-funding service is deliberately left running and
    untouched; it keeps writing bybit_funding_snapshots. This adapter is an
    additional reader of the same public endpoints (2 calls / 300s), which is
    far cheaper than risking a change to a collector that already works.
    """

    name = "bybit"
    B = "https://api.bybit.com/v5/market/"
    I_PERP = B + "instruments-info?category=linear&limit=1000"
    I_SPOT = B + "instruments-info?category=spot&limit=1000"
    T_PERP = B + "tickers?category=linear"
    T_SPOT = B + "tickers?category=spot"

    async def refresh(self, session, now_mono: float) -> int:
        try:
            lin = await get_json(session, self.I_PERP)
            spo = await get_json(session, self.I_SPOT)
        except Exception as exc:                      # noqa: BLE001
            log.warning("[venues/bybit] refresh failed: %r (keeping %d)",
                        exc, len(self.universe))
            return 0
        spot = {i["symbol"] for i in spo["result"]["list"] if i.get("status") == "Trading"}
        uni = {}
        for i in lin["result"]["list"]:
            if (i.get("contractType") != "LinearPerpetual" or i.get("status") != "Trading"
                    or i.get("quoteCoin") != "USDT" or i["symbol"] not in spot):
                continue
            mins = f(i.get("fundingInterval"))
            if not mins:
                continue
            uni[i["symbol"]] = {"iv": mins / 60.0}
        if uni:
            self.universe = uni
            self._last_refresh = now_mono
            log.info("[venues/bybit] universe: %d dual-listed USDT perps", len(uni))
        return len(uni)

    async def snapshot(self, session) -> tuple[list[Row], dict, list]:
        res, failures = await gather_isolated(session, [self.T_PERP, self.T_SPOT])
        tp, ts = res
        stats = {"unpriced": 0, "unit_skip": 0, "no_interval": 0}
        if tp is None or ts is None:
            return [], stats, failures
        pt = {x["symbol"]: x for x in tp["result"]["list"]}
        st = {x["symbol"]: x for x in ts["result"]["list"]}

        rows: list[Row] = []
        for sym, meta in self.universe.items():
            p, s = pt.get(sym), st.get(sym)
            if p is None or s is None:
                continue
            perp_bid, perp_ask = f(p.get("bid1Price")), f(p.get("ask1Price"))
            spot_bid, spot_ask = f(s.get("bid1Price")), f(s.get("ask1Price"))
            spot_price = mid(spot_bid, spot_ask)
            perp_mark = f(p.get("markPrice"))
            if perp_mark is None or spot_price is None:
                stats["unpriced"] += 1
                continue
            if not unit_gate(perp_mark, spot_price):
                stats["unit_skip"] += 1
                continue
            nft = f(p.get("nextFundingTime"))
            nxt = datetime.fromtimestamp(nft / 1000.0, timezone.utc) if nft else None
            rows.append(Row(
                exchange="bybit", symbol=sym, perp_mark=perp_mark,
                spot_price=spot_price, funding_rate=f(p.get("fundingRate")),
                interval_h=meta["iv"], next_settle=nxt,
                interval_source="bybit.instruments.fundingInterval",
                perp_bid=perp_bid, perp_ask=perp_ask,
                spot_bid=spot_bid, spot_ask=spot_ask,
                perp_vol_base=f(p.get("volume24h")), perp_vol_usd=f(p.get("turnover24h")),
                perp_oi=f(p.get("openInterest")),
                spot_vol_base=f(s.get("volume24h")), spot_vol_usd=f(s.get("turnover24h")),
                perp_bid_size=f(p.get("bid1Size")), perp_ask_size=f(p.get("ask1Size")),
                spot_bid_size=f(s.get("bid1Size")), spot_ask_size=f(s.get("ask1Size")),
                contract_multiplier=None,             # qty is base coin
                perp_index_price=f(p.get("indexPrice")),
            ))
        return rows, stats, failures


ADAPTERS = [OkxAdapter, BitgetAdapter, KucoinAdapter, BybitAdapter]
