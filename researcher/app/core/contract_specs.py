"""
Contract specifications — per-exchange size multipliers for correct USD notional.

Gate.io and MEXC futures order-book / trade sizes are quoted in CONTRACTS, not base
coin units. Each contract represents `multiplier` base-coin units:
  - Gate:  quanto_multiplier  (GET /futures/usdt/contracts)
  - MEXC:  contractSize        (GET /api/v1/contract/detail)

USD notional of a book level = price × size × multiplier.

Multipliers vary per contract (Gate seen: 0.0001 … 10000), so a single hardcoded
convention (price×size, or 1 contract = 1 USDT) is always wrong for some coins.
We fetch the real specs once at startup and cache them in memory. Symbols with no
spec are left absent → callers get None and must skip (never fake with wrong units).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_GATE_CONTRACTS_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
_MEXC_DETAIL_URL    = "https://contract.mexc.com/api/v1/contract/detail"


class ContractSpecs:
    """In-memory cache of per-(exchange, symbol) size multipliers."""

    def __init__(self) -> None:
        # (exchange, symbol_upper) -> multiplier (base-coin units per contract)
        self._mult: dict[tuple[str, str], float] = {}

    def get(self, exchange: str, symbol: str) -> Optional[float]:
        """Multiplier for one contract, or None if we have no spec for it."""
        return self._mult.get((exchange, symbol.upper()))

    def __len__(self) -> int:
        return len(self._mult)

    async def load(self) -> None:
        """Fetch Gate + MEXC contract specs. Never raises — a failed exchange is
        simply left absent (its symbols then report None → depth USD None)."""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await self._load_gate(session)
            await self._load_mexc(session)
        logger.info("[ContractSpecs] Loaded %d multipliers total (gate+mexc)", len(self._mult))

    async def _load_gate(self, session) -> None:
        try:
            async with session.get(_GATE_CONTRACTS_URL) as resp:
                data = await resp.json()
            n = 0
            for c in data:
                name = c.get("name")
                qm   = c.get("quanto_multiplier")
                if not name or qm is None:
                    continue
                try:
                    m = float(qm)
                except (TypeError, ValueError):
                    continue
                if m > 0:
                    self._mult[("gate", name.upper())] = m
                    n += 1
            logger.info("[ContractSpecs] Gate: %d contract multipliers", n)
        except Exception as exc:
            logger.warning(
                "[ContractSpecs] Gate spec fetch failed: %r — gate depth USD will be None", exc
            )

    async def _load_mexc(self, session) -> None:
        try:
            async with session.get(_MEXC_DETAIL_URL) as resp:
                payload = await resp.json()
            data = payload.get("data", payload) if isinstance(payload, dict) else payload
            n = 0
            for c in data:
                sym = c.get("symbol")
                cs  = c.get("contractSize")
                if not sym or cs is None:
                    continue
                try:
                    m = float(cs)
                except (TypeError, ValueError):
                    continue
                if m > 0:
                    self._mult[("mexc", sym.upper())] = m
                    n += 1
            logger.info("[ContractSpecs] MEXC: %d contract multipliers", n)
        except Exception as exc:
            logger.warning(
                "[ContractSpecs] MEXC spec fetch failed: %r — mexc depth USD will be None", exc
            )
