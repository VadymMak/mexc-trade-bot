# app/utils/idempotency.py
"""
Idempotency decorator for FastAPI endpoints.
Automatically caches responses based on X-Idempotency-Key header.
"""
import functools
from typing import Optional, Callable, Any
from fastapi import Header, HTTPException, status

from app.services.idempotency import get_idempotency_manager
from app.services.logger import get_logger
from app.config.settings import settings

logger = get_logger(__name__)


def idempotent(ttl_seconds: Optional[int] = None):
    """
    Decorator for idempotent endpoints.
    
    Caches response based on X-Idempotency-Key header and workspace_id.
    If key is provided and found in cache, returns cached response immediately.
    
    Args:
        ttl_seconds: Cache TTL in seconds (default: from settings)
    
    Usage:
        @router.post("/api/exec/place")
        @idempotent(ttl_seconds=600)
        async def place_order(
            payload: dict,
            x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key")
        ):
            # Your endpoint logic
            return {"order_id": "123", "status": "filled"}
    
    Response format:
        - Normal execution: {"order_id": "123", "status": "filled"}
        - Cached response: {
            "order_id": "123",
            "status": "filled",
            "idempotent": True,
            "cached_at": 1737123456,
            "cache_age_sec": 5.23
          }
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Check if idempotency is enabled
            if not settings.idempotency_enabled:
                # If disabled, just execute function normally
                return await func(*args, **kwargs)
            
            # Extract idempotency key from kwargs
            idem_key: Optional[str] = kwargs.get("x_idempotency_key")
            
            # If no key provided, execute normally (idempotency is optional)
            if not idem_key:
                logger.debug(f"No idempotency key provided for {func.__name__}, executing normally")
                return await func(*args, **kwargs)
            
            # Get workspace_id (assume it's in settings for now)
            workspace_id = settings.workspace_id
            
            # Get idempotency manager
            manager = get_idempotency_manager()
            
            # Check cache
            cached_response = await manager.get(idem_key, workspace_id)
            if cached_response is not None:
                logger.info(
                    f"Idempotent request detected: endpoint={func.__name__}, "
                    f"key={idem_key[:16]}..., returning cached response"
                )
                return cached_response
            
            # Cache miss - execute function
            logger.debug(f"Idempotency cache miss for {func.__name__}, executing function")
            
            try:
                # Execute the actual endpoint function
                response = await func(*args, **kwargs)
                
                # Cache the response
                ttl = ttl_seconds if ttl_seconds is not None else settings.idempotency_ttl_seconds
                await manager.set(idem_key, workspace_id, response, ttl=ttl)
                
                logger.info(
                    f"Response cached: endpoint={func.__name__}, "
                    f"key={idem_key[:16]}..., ttl={ttl}s"
                )
                
                return response
                
            except Exception as e:
                # Don't cache errors
                logger.error(
                    f"Error in idempotent endpoint {func.__name__}: {e}",
                    exc_info=True
                )
                raise
        
        return wrapper
    return decorator


def extract_idempotency_key(x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key")) -> Optional[str]:
    """
    FastAPI dependency to extract X-Idempotency-Key header.
    
    Usage:
        @router.post("/api/exec/place")
        async def place_order(
            payload: dict,
            idem_key: Optional[str] = Depends(extract_idempotency_key)
        ):
            ...
    """
    return x_idempotency_key


def get_idempotency_key(
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key")
) -> Optional[str]:
    """
    FastAPI dependency to extract idempotency key from request header.
    
    Usage in route:
        @router.post("/place")
        async def place_order(
            payload: dict,
            idempotency_key: Optional[str] = Depends(get_idempotency_key)
        ):
            ...
    """
    return x_idempotency_key