# app/services/strategy_service.py
from __future__ import annotations

import json
import hashlib
import time
from typing import Any, Awaitable, Callable, Dict, Tuple, Optional
from sqlalchemy.orm import Session  # Для DB access в stop_all_symbols

from app.db.session import SessionLocal  # Для local session
from app.models.strategy_state import StrategyState  # Assume модель для strategy_state table


class StrategyService:
    """
    Простейший in-memory сервис идемпотентности (на процесс) + базовая логика стратегий.
    - Ключ неймспейсится по op_name: (op_name, idempotency_key)
    - Для payload считаем стабильный JSON-хэш (sort_keys=True)
    - TTL-очистка и мягкий лимит размера кэша
    - stop_all_symbols: очищает strategy_state в DB (stop all per-symbol strategies)
    """

    _instance = None  # Singleton

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, ttl_seconds: int = 30, max_entries: int = 2048):
        if hasattr(self, '_initialized'):
            return  # Уже init
        self._initialized = True
        self.ttl_seconds = int(ttl_seconds)
        self.max_entries = int(max_entries)
        # store[(op_name, key)] = {"hash": str, "result": dict, "ts": float}
        self._store: Dict[Tuple[str, str], Dict[str, Any]] = {}

    @classmethod
    def get(cls) -> "StrategyService":
        """Singleton getter for hooks."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _norm_key(s: str) -> str:
        return (s or "").strip()

    @staticmethod
    def _hash_payload(payload: Dict[str, Any]) -> str:
        dumped = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    def _gc(self, now: float) -> None:
        """TTL-очистка + мягкое ограничение по размеру."""
        ttl = self.ttl_seconds
        if ttl > 0:
            self._store = {k: v for k, v in self._store.items() if now - v.get("ts", 0.0) <= ttl}
        # мягкий лимит: если много записей — усечём самые старые
        overflow = len(self._store) - self.max_entries
        if overflow > 0:
            # сортировка по ts возр.
            items = sorted(self._store.items(), key=lambda kv: kv[1].get("ts", 0.0))
            for k, _ in items[:overflow]:
                self._store.pop(k, None)

    async def execute_idempotent(
        self,
        op_name: str,
        idempotency_key: str,
        payload: Dict[str, Any],
        action: Callable[[], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        now = time.time()
        self._gc(now)

        op = self._norm_key(op_name) or "default"
        key = self._norm_key(idempotency_key)
        if not key:
            # без ключа — просто выполняем действие (для совместимости)
            result = await action()
            out = dict(result)
            out.setdefault("idempotent", False)
            return out

        storage_key = (op, key)
        payload_hash = self._hash_payload(payload)

        if storage_key in self._store:
            entry = self._store[storage_key]
            if entry["hash"] != payload_hash:
                return {
                    "ok": False,
                    "error": "IdempotencyKeyConflict",
                    "detail": f"Key {key} reused with different payload",
                }
            out = dict(entry["result"])
            out["idempotent"] = True
            return out

        # Выполнение действия
        result = await action()
        out = dict(result)
        out["idempotent"] = False

        # Сохранение
        self._store[storage_key] = {
            "hash": payload_hash,
            "result": out,
            "ts": now,
        }
        return out

    async def stop_all_symbols(self, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Stop all symbols (clear strategy_state in DB).
        Returns {'stopped': n_symbols, 'ok': True}.
        """
        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        try:
            # Clear strategy_state table (set active=False or delete)
            stopped = db.query(StrategyState).update({StrategyState.active: False})  # Или .delete()
            db.commit()
            return {"ok": True, "stopped": stopped}
        except Exception as e:
            if close_db:
                db.rollback()
            return {"ok": False, "error": str(e)}
        finally:
            if close_db:
                db.close()