# app/services/sse_publisher.py
"""
Lightweight shim to publish server-sent events without coupling the PnL service
to the concrete implementation inside app/api/stream.py.

It tries a few common shapes so you don't have to change existing stream code:
- publish(event_type: str, payload: dict)
- publish(message: dict)      # where dict contains "event"/"type" and "data"/"payload"
- broadcast(message: dict)    # some apps expose a broadcaster instead

If none is available, calls become no-ops (they must never break accounting).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Try to locate a publish/broadcast function in your stream module
_stream_publish = None  # type: Optional[Any]
_broadcast = None       # type: Optional[Any]

__all__ = ["publish"]

try:  # pragma: no cover
    # Most likely: a function we can call directly
    from app.api.stream import publish as _stream_publish  # type: ignore
except Exception:  # pragma: no cover
    try:
        # Alternative: a broadcaster-like callable
        from app.api.stream import broadcast as _broadcast  # type: ignore
    except Exception:
        pass


def _coerce_message(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize to a flexible envelope that typical SSE consumers understand.
    """
    return {
        "event": event_type,   # some clients expect "event" or "type"
        "type": event_type,
        "data": payload,       # some clients expect "data" or "payload"
        "payload": payload,
    }


def publish(event_type: str, payload: Dict[str, Any]) -> None:
    """
    Fire-and-forget SSE emit. Never raise if the downstream is missing.
    Tries multiple call shapes to match your existing stream implementation.
    """
    msg = _coerce_message(event_type, payload)

    # Try the most explicit signature first
    if _stream_publish is not None:
        try:  # (event_type, payload)
            _stream_publish(event_type, payload)  # type: ignore
            return
        except TypeError:
            pass
        try:  # (message: dict)
            _stream_publish(msg)  # type: ignore
            return
        except Exception:
            pass

    # Try a broadcaster-style callable that takes a single message
    if _broadcast is not None:
        try:
            _broadcast(msg)  # type: ignore
            return
        except Exception:
            pass

    # Last resort: silently no-op
    return
