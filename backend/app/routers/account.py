from __future__ import annotations
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Depends

from app.utils.idempotency import idempotent, get_idempotency_key
from app.services.exchange_private import get_private_client

router = APIRouter(prefix="/api/account", tags=["account"])


def _to_dict(x: Any) -> Dict[str, Any]:
    if is_dataclass(x):
        return asdict(x)
    for attr in ("model_dump", "dict"):
        m = getattr(x, attr, None)
        if callable(m):
            return m()
    return dict(getattr(x, "__dict__", {}))


def _norm_res(res: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Нормализуем ответ close_all до словаря и добавим поле ok при отсутствии."""
    if not isinstance(res, dict):
        return {"ok": False, "error": "provider returned no data"}
    if "ok" not in res:
        res["ok"] = not bool(res.get("errors"))  # ok, если нет ошибок
    return res


@router.get("/balances")
async def get_balances() -> List[dict]:
    try:
        async with get_private_client() as client:
            bals = await client.fetch_balances()
            return [_to_dict(b) for b in bals]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Balance fetch failed: {e!s}")


@router.get("/positions")
async def get_positions() -> List[dict]:
    """Spot: позиции выводим по немаржинальным остаткам base-активов."""
    try:
        async with get_private_client() as client:
            pos = await client.fetch_positions()
            return [_to_dict(p) for p in pos]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Positions fetch failed: {e!s}")


@router.post("/close_all")
@idempotent(ttl_seconds=600)
async def close_all_positions(
    use_market: bool = Query(True),
    x_idempotency_key: Optional[str] = Depends(get_idempotency_key),  # ← ADD THIS LINE
) -> Dict[str, Any]:
    try:
        async with get_private_client() as client:
            res = await client.close_all_positions(use_market=use_market)
            res = _norm_res(res)
            if not res.get("ok", False):
                # 400 — когда мы дошли до провайдера, но операция не удалась
                raise HTTPException(status_code=400, detail=res)
            return res
    except HTTPException:
        raise
    except Exception as e:
        # 502 — внутренний сбой клиента/сети/подписей и пр.
        raise HTTPException(status_code=502, detail=f"Close-all failed: {e!s}")