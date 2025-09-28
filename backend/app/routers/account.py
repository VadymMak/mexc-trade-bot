# app/routers/account.py
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from app.services.exchange_private import get_private_client

router = APIRouter(prefix="/api/account", tags=["account"])


def _to_dict(x: Any) -> Dict[str, Any]:
    if is_dataclass(x):
        return asdict(x)
    # pydantic v2 models: .model_dump(), v1: .dict()
    for attr in ("model_dump", "dict"):
        m = getattr(x, attr, None)
        if callable(m):
            return m()
    # best effort
    return dict(getattr(x, "__dict__", {}))


@router.get("/balances")
async def get_balances() -> List[dict]:
    client = get_private_client()
    try:
        bals = await client.fetch_balances()
        return [_to_dict(b) for b in bals]
    except HTTPException:
        raise
    except Exception as e:
        # Convert provider errors into a clean 502
        raise HTTPException(status_code=502, detail=f"Balance fetch failed: {e!s}")
    finally:
        await client.aclose()


@router.get("/positions")
async def get_positions() -> List[dict]:
    """
    Spot: derived from balances for the active provider.
    (Gate: any non-USDT asset with qty>0 is a 'position'.)
    """
    client = get_private_client()
    try:
        pos = await client.fetch_positions()
        return [_to_dict(p) for p in pos]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Positions fetch failed: {e!s}")
    finally:
        await client.aclose()


@router.post("/close_all")
async def close_all_positions(use_market: bool = Query(True)) -> Dict[str, Any]:
    client = get_private_client()
    try:
        res = await client.close_all_positions(use_market=use_market)
        if not res.get("ok", False):
            # bubble provider reason if present
            raise HTTPException(status_code=400, detail=res)
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Close-all failed: {e!s}")
    finally:
        await client.aclose()
