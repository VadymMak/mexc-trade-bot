# app/services/gate_private.py
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config.settings import settings
from app.services.exchange_private import (
    BalanceInfo,
    PositionInfo,
    OrderRequest,
    OrderResult,
    ExchangePrivate,
)


def _env(name: str, default: str = "") -> str:
    import os
    return os.getenv(name, default)


def _gate_conf() -> Dict[str, str]:
    """
    Choose live vs testnet using settings flags; fall back to env.
    """
    is_demo = bool(getattr(settings, "is_demo", False) or getattr(settings, "is_paper", False))
    if is_demo:
        return {
            "key": getattr(settings, "gate_testnet_api_key", None) or _env("GATE_TESTNET_API_KEY", ""),
            "sec": getattr(settings, "gate_testnet_api_secret", None) or _env("GATE_TESTNET_API_SECRET", ""),
            "base": (getattr(settings, "gate_testnet_rest_base", None)
                     or _env("GATE_TESTNET_REST_BASE", "https://api-testnet.gateapi.io/api/v4")).rstrip("/"),
        }
    return {
        "key": getattr(settings, "gate_api_key", None) or _env("GATE_API_KEY", ""),
        "sec": getattr(settings, "gate_api_secret", None) or _env("GATE_API_SECRET", ""),
        "base": (getattr(settings, "gate_rest_base", None)
                 or _env("GATE_REST_BASE", "https://api.gateio.ws/api/v4")).rstrip("/"),
    }


def _ts() -> str:
    # Gate expects seconds since epoch as a string
    return str(int(time.time()))


def _hmac_sha512(secret: str, msg: str) -> str:
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha512).hexdigest()


def _encode_query(q: Optional[Dict[str, Any]]) -> str:
    if not q:
        return ""
    parts: List[str] = []
    for k in sorted(q.keys()):
        v = q[k]
        if v is None:
            continue
        parts.append(f"{k}={v}")
    return "&".join(parts)


def _to_text_tag(tag: Optional[str]) -> str:
    # Gate requires "text" to start with "t-"
    if not tag:
        return "t-app"
    s = str(tag)
    return s if s.startswith("t-") else f"t-{s}"


def _parse_dt_ms(ms: Any) -> Optional[datetime]:
    try:
        # Gate sometimes returns string seconds; we prioritize *_time_ms if available
        v = float(ms)
        if v > 1e12:  # already milliseconds
            t = v / 1000.0
        else:
            t = v  # seconds
        return datetime.fromtimestamp(t, tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


class GatePrivate(ExchangePrivate):
    """
    Gate.io Spot private client (LIVE/TESTNET via settings).
    - Spot “positions” are derived from balances (non-USDT assets with qty > 0).
    - MARKET or LIMIT buy/sell.
    """

    def __init__(self) -> None:
        cfg = _gate_conf()
        self._key = cfg["key"]
        self._sec = cfg["sec"]
        self._base = cfg["base"]
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
        self._quote = "USDT"

    # ---------- context mgmt ----------
    async def __aenter__(self) -> "GatePrivate":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ---------- symbol helpers ----------

    def normalize_symbol(self, symbol: str) -> str:
        # Gate uses "ETH_USDT"; app uses "ETHUSDT"
        s = (symbol or "").upper().replace("-", "").replace("/", "")
        return s.replace("_", "")

    def provider_symbol(self, symbol: str) -> str:
        s = self.normalize_symbol(symbol)
        # Prefer mapping …USDT → BASE_USDT; fallback split of last 4 chars
        if s.endswith(self._quote) and len(s) > len(self._quote):
            base = s[: -len(self._quote)]
            return f"{base}_{self._quote}"
        return f"{s[:-4]}_{s[-4:]}" if len(s) > 4 else s

    # ---------- signing ----------

    def _signed_headers(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        ts = _ts()
        qstr = _encode_query(query)
        body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""
        msg = "\n".join([method.upper(), path, qstr, body_json, ts])
        sign = _hmac_sha512(self._sec, msg)
        return {
            "KEY": self._key,
            "Timestamp": ts,
            "SIGN": sign,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ---------- low-level requests ----------

    async def _get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Any:
        headers = self._signed_headers("GET", path, query=query)
        r = await self._client.get(path, params=query, headers=headers)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, body: Dict[str, Any]) -> Any:
        headers = self._signed_headers("POST", path, body=body)
        r = await self._client.post(path, content=json.dumps(body), headers=headers)
        r.raise_for_status()
        return r.json()

    async def _delete(self, path: str, query: Optional[Dict[str, Any]] = None) -> Any:
        headers = self._signed_headers("DELETE", path, query=query)
        r = await self._client.delete(path, params=query, headers=headers)
        r.raise_for_status()
        return r.json()

    # ---------- account ----------

    async def fetch_balances(self) -> List[BalanceInfo]:
        data = await self._get("/spot/accounts")
        out: List[BalanceInfo] = []
        for it in data or []:
            try:
                cur = str(it.get("currency", "")).upper()
                free = float(it.get("available", 0) or 0)
                locked = float(it.get("locked", 0) or 0)
                out.append(BalanceInfo(asset=cur, free=free, locked=locked, total=free + locked))
            except Exception:
                continue
        return out

    async def fetch_positions(self) -> List[PositionInfo]:
        bals = await self.fetch_balances()
        now_ms = int(time.time() * 1000)
        pos: List[PositionInfo] = []
        for b in bals:
            if b.asset == self._quote:
                continue
            qty = (b.total if b.total is not None else (b.free + b.locked)) or 0.0
            if qty <= 0:
                continue
            symbol = f"{b.asset}{self._quote}"
            pos.append(
                PositionInfo(
                    symbol=symbol,
                    qty=qty,
                    avg_price=0.0,       # Gate doesn’t provide average entry for spot balances
                    unrealized_pnl=0.0,  # can be computed with quotes in UI
                    realized_pnl=0.0,
                    ts_ms=now_ms,
                )
            )
        return pos

    # ---------- orders ----------

    async def place_order(self, req: OrderRequest) -> OrderResult:
        pair = self.provider_symbol(req.symbol)
        side = str(req.side or "").lower()
        if side not in {"buy", "sell"}:
            return OrderResult(ok=False, status="REJECTED", raw={"reason": "side must be BUY or SELL"})

        typ = "limit" if (str(req.type or "").upper() == "LIMIT" or req.price) else "market"

        body: Dict[str, Any] = {
            "currency_pair": pair,
            "account": "spot",
            "side": side,
            "type": typ,
            "amount": f"{req.qty}",
            "text": _to_text_tag(req.tag),
        }
        if typ == "limit":
            if not req.price or req.price <= 0:
                return OrderResult(ok=False, status="REJECTED", raw={"reason": "limit price required"})
            body["price"] = f"{req.price}"
        if req.tif:
            body["time_in_force"] = str(req.tif).lower()

        try:
            resp = await self._post("/spot/orders", body)
        except httpx.HTTPStatusError as e:
            return OrderResult(
                ok=False,
                status="HTTP_ERROR",
                raw={"code": e.response.status_code, "body": e.response.text},
            )
        except Exception as e:
            return OrderResult(ok=False, status="ERROR", raw={"msg": str(e)})

        # Extract additional fields for PnL / fee accounting
        executed_at = (
            _parse_dt_ms(resp.get("update_time_ms"))
            or _parse_dt_ms(resp.get("create_time_ms"))
            or _parse_dt_ms(resp.get("update_time"))
            or _parse_dt_ms(resp.get("create_time"))
        )
        fee = None
        fee_asset = None
        try:
            # Gate commonly returns "fee" and "fee_currency" on order or on each deal
            if resp.get("fee") is not None:
                fee = float(resp.get("fee"))
            if resp.get("fee_currency"):
                fee_asset = str(resp.get("fee_currency")).upper()
        except Exception:
            pass

        trade_id = None
        try:
            # Some payloads include last deal id or in "deals":[]
            deals = resp.get("deal_list") or resp.get("deals") or []
            if isinstance(deals, list) and deals:
                trade_id = str(deals[-1].get("id") or deals[-1].get("trade_id") or "")
        except Exception:
            pass

        return OrderResult(
            ok=True,
            client_order_id=str(resp.get("text") or ""),
            exchange_order_id=str(resp.get("id") or ""),
            status=str(resp.get("status") or ""),
            filled_qty=float(resp.get("filled_amount") or 0),
            avg_fill_price=float(resp.get("avg_deal_price") or resp.get("fill_price") or 0),
            executed_at=executed_at,
            fee=fee,
            fee_asset=fee_asset,
            trade_id=trade_id,
            raw=resp,
        )

    async def cancel_order(
        self,
        symbol: str,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> bool:
        pair = self.provider_symbol(symbol)
        order_id = exchange_order_id

        if not order_id:
            # Try to backfill by matching "text"
            try:
                opens = await self.get_open_orders(symbol)
            except Exception:
                opens = []
            if client_order_id:
                for o in opens:
                    if str(o.get("text") or "") == client_order_id:
                        order_id = str(o.get("id"))
                        break
            if not order_id and opens:
                order_id = str(opens[0].get("id"))

        if not order_id:
            return False

        try:
            await self._delete(f"/spot/orders/{order_id}", {"currency_pair": pair})
            return True
        except Exception:
            return False

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"account": "spot"}
        if symbol:
            query["currency_pair"] = self.provider_symbol(symbol)
        try:
            resp = await self._get("/spot/open_orders", query)
            return resp if isinstance(resp, list) else []
        except Exception:
            return []

    # ---------- convenience ----------

    async def close_all_positions(self, use_market: bool = True) -> Dict[str, Any]:
        bals = await self.fetch_balances()
        tasks: List[asyncio.Task[OrderResult]] = []

        for b in bals:
            if b.asset == self._quote:
                continue
            qty = (b.total if b.total is not None else (b.free + b.locked)) or 0.0
            if qty <= 0:
                continue
            req = OrderRequest(
                symbol=f"{b.asset}{self._quote}",
                side="SELL",
                qty=qty,
                price=None,
                type="MARKET" if use_market else "LIMIT",
                tif="ioc" if use_market else None,
                tag="close_all",
            )
            tasks.append(asyncio.create_task(self.place_order(req)))

        results: List[OrderResult] = []
        if tasks:
            done = await asyncio.gather(*tasks, return_exceptions=True)
            for d in done:
                if isinstance(d, Exception):
                    results.append(OrderResult(ok=False, status="ERROR", raw={"msg": str(d)}))
                else:
                    results.append(d)

        return {
            "ok": all(r.ok for r in results) if results else True,
            "results": [asdict(r) for r in results],
        }

    # ---------- cleanup ----------

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass
