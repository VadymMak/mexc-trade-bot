# app/services/idempotency.py
"""
Idempotency manager for mutation endpoints.
Caches (key, workspace_id) → response for configurable TTL to prevent duplicate operations.
"""
import time
import asyncio
from typing import Optional, Any
from dataclasses import dataclass
from collections import OrderedDict

from app.config.settings import settings
from app.services.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CachedResponse:
    """Cached idempotent response with metadata."""
    response: dict[str, Any]
    cached_at: float  # Unix timestamp
    ttl_sec: int
    
    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return time.time() > (self.cached_at + self.ttl_sec)
    
    def age_seconds(self) -> float:
        """Return age of cache entry in seconds."""
        return time.time() - self.cached_at


class IdempotencyManager:
    """
    In-memory idempotency cache with TTL.
    Thread-safe via asyncio.Lock.
    
    Usage:
        manager = IdempotencyManager()
        
        # Check cache
        cached = await manager.get("order-123", workspace_id=1)
        if cached:
            return cached  # Return cached response
        
        # Execute operation
        result = await place_order(...)
        
        # Store in cache
        await manager.set("order-123", workspace_id=1, response=result, ttl=600)
    """
    
    def __init__(self):
        # Cache: (key, workspace_id) → CachedResponse
        self._cache: OrderedDict[tuple[str, int], CachedResponse] = OrderedDict()
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._enabled = settings.idempotency_enabled
        self._default_ttl = settings.idempotency_ttl_seconds
        
        logger.info(
            f"IdempotencyManager initialized: "
            f"enabled={self._enabled}, default_ttl={self._default_ttl}s, "
            f"backend={settings.idempotency_backend}"
        )
    
    async def start(self):
        """Start background cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Idempotency cleanup task started")
    
    async def stop(self):
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Idempotency cleanup task stopped")
    
    async def get(
        self,
        key: str,
        workspace_id: int
    ) -> Optional[dict[str, Any]]:
        """
        Get cached response for idempotency key.
        
        Args:
            key: Idempotency key (e.g., UUID from X-Idempotency-Key header)
            workspace_id: Workspace identifier
        
        Returns:
            Cached response dict if found and not expired, else None
        """
        if not self._enabled:
            return None
        
        if not key:
            return None
        
        cache_key = (key, workspace_id)
        
        async with self._lock:
            entry = self._cache.get(cache_key)
            
            if entry is None:
                return None
            
            # Check expiration
            if entry.is_expired():
                del self._cache[cache_key]
                logger.debug(
                    f"Idempotency key expired: key={key[:16]}..., "
                    f"workspace={workspace_id}, age={entry.age_seconds():.1f}s"
                )
                return None
            
            # Cache hit
            logger.info(
                f"Idempotency cache HIT: key={key[:16]}..., "
                f"workspace={workspace_id}, age={entry.age_seconds():.1f}s"
            )
            
            # Return response with metadata
            response = entry.response.copy()
            response["idempotent"] = True
            response["cached_at"] = int(entry.cached_at)
            response["cache_age_sec"] = round(entry.age_seconds(), 2)
            
            return response
    
    async def set(
        self,
        key: str,
        workspace_id: int,
        response: dict[str, Any],
        ttl: Optional[int] = None
    ):
        """
        Cache response for idempotency key.
        
        Args:
            key: Idempotency key
            workspace_id: Workspace identifier
            response: Response dict to cache
            ttl: Time-to-live in seconds (default: from settings)
        """
        if not self._enabled:
            return
        
        if not key:
            return
        
        ttl_sec = ttl if ttl is not None else self._default_ttl
        cache_key = (key, workspace_id)
        
        async with self._lock:
            entry = CachedResponse(
                response=response.copy(),
                cached_at=time.time(),
                ttl_sec=ttl_sec
            )
            self._cache[cache_key] = entry
            
            logger.info(
                f"Idempotency cached: key={key[:16]}..., "
                f"workspace={workspace_id}, ttl={ttl_sec}s"
            )
    
    async def clear(self, key: Optional[str] = None, workspace_id: Optional[int] = None):
        """
        Clear cache entries.
        
        Args:
            key: If provided, clear only this key for all workspaces
            workspace_id: If provided (with key), clear only this specific entry
        """
        async with self._lock:
            if key is None and workspace_id is None:
                # Clear all
                count = len(self._cache)
                self._cache.clear()
                logger.info(f"Idempotency cache cleared: {count} entries removed")
            elif key and workspace_id:
                # Clear specific entry
                cache_key = (key, workspace_id)
                if cache_key in self._cache:
                    del self._cache[cache_key]
                    logger.debug(f"Idempotency entry cleared: key={key[:16]}..., workspace={workspace_id}")
            elif key:
                # Clear all entries for this key across workspaces
                keys_to_remove = [k for k in self._cache.keys() if k[0] == key]
                for k in keys_to_remove:
                    del self._cache[k]
                logger.debug(f"Idempotency entries cleared for key={key[:16]}...: {len(keys_to_remove)} entries")
    
    async def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        async with self._lock:
            total = len(self._cache)
            expired = sum(1 for entry in self._cache.values() if entry.is_expired())
            active = total - expired
            
            return {
                "enabled": self._enabled,
                "backend": settings.idempotency_backend,
                "total_entries": total,
                "active_entries": active,
                "expired_entries": expired,
                "default_ttl_sec": self._default_ttl
            }
    
    async def _cleanup_loop(self):
        """Background task to remove expired entries."""
        cleanup_interval = 60  # Run every 60 seconds
        
        while True:
            try:
                await asyncio.sleep(cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Idempotency cleanup error: {e}", exc_info=True)
    
    async def _cleanup_expired(self):
        """Remove expired entries from cache."""
        async with self._lock:
            keys_to_remove = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]
            
            for key in keys_to_remove:
                del self._cache[key]
            
            if keys_to_remove:
                logger.debug(f"Idempotency cleanup: removed {len(keys_to_remove)} expired entries")


# Global singleton instance
_manager: Optional[IdempotencyManager] = None


def get_idempotency_manager() -> IdempotencyManager:
    """Get or create global idempotency manager instance."""
    global _manager
    if _manager is None:
        _manager = IdempotencyManager()
    return _manager


async def start_idempotency_manager():
    """Start idempotency manager (call on app startup)."""
    manager = get_idempotency_manager()
    await manager.start()


async def stop_idempotency_manager():
    """Stop idempotency manager (call on app shutdown)."""
    manager = get_idempotency_manager()
    await manager.stop()
