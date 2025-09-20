from __future__ import annotations
import gzip
from typing import Any, Iterator, Tuple, Optional, Callable

from .proto_helpers import (
    iter_message_fields,
    collect_bytes_candidates,
)

_FIELD_NAMES_CHANNEL = ("channel", "topic", "ch")
_FIELD_NAMES_SYMBOL = ("symbol", "instId", "s")
_FIELD_NAMES_SENDTS = ("sendTime", "sendtime", "ts", "time", "t")
_FIELD_NAMES_DATA   = ("data", "payload", "d")

def hexdump(b: bytes, n: int = 48) -> str:
    return " ".join(f"{x:02X}" for x in b[:n])

def maybe_gunzip(payload: bytes) -> tuple[bytes, bool]:
    if len(payload) >= 2 and payload[0] == 0x1F and payload[1] == 0x8B:
        try:
            return gzip.decompress(payload), True
        except Exception:
            return payload, False
    return payload, False

def get_attr_any(obj: Any, names: tuple[str, ...], default: Any = None) -> Any:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default

def extract_frames(
    obj: Any,
    debug_cb: Optional[Callable[[str], None]] = None
) -> Iterator[Tuple[str, str, int, bytes]]:
    """
    Yields (channel, symbol, send_ts, payload_bytes).
    Strategy:
      1) Если в wrapper установлены саб-месседжи (ListFields) — сериализуем их напрямую.
      2) Иначе: bytes-поля верхнего уровня.
      3) Иначе: ищем вложенные bytes и берём лучший кандидат.
      4) Иначе: рекурсивно углубляемся.
    """
    ch  = get_attr_any(obj, _FIELD_NAMES_CHANNEL, "") or ""
    sym = get_attr_any(obj, _FIELD_NAMES_SYMBOL, "") or ""
    ts  = get_attr_any(obj, _FIELD_NAMES_SENDTS, 0) or 0

    # 1) filled sub-messages
    try:
        yielded = False
        for name, val, is_rep in iter_message_fields(obj):
            if hasattr(val, "DESCRIPTOR") and hasattr(val, "ListFields") and val.ListFields():
                try:
                    payload = val.SerializeToString()
                    ch_eff = ch or name
                    if debug_cb:
                        debug_cb(f"🎯 Selected submessage '{name}' → bytes={len(payload)}")
                    yield str(ch_eff), str(sym), int(ts), payload
                    yielded = True
                except Exception:
                    pass
        if yielded:
            return
    except Exception:
        pass

    # 2) top-level bytes
    data = get_attr_any(obj, _FIELD_NAMES_DATA, None)
    if isinstance(data, (bytes, bytearray)) and len(data) > 0:
        yield str(ch), str(sym), int(ts), bytes(data)
        return

    # 3) nested bytes candidates
    cands = collect_bytes_candidates(obj)
    if cands:
        name_priority = ("payload", "data", "content", "body", "message", "binary", "raw")
        def score(item: tuple[str, bytes]) -> tuple[int, int]:
            path, b = item
            pri = max((len(name_priority) - i)
                      for i, key in enumerate(name_priority)
                      if key in path.lower()) if any(k in path.lower() for k in name_priority) else 0
            return (pri, len(b))
        best_path, best_bytes = max(cands, key=score)
        if debug_cb:
            debug_cb(f"🎯 Selected inner payload path={best_path} len={len(best_bytes)}")
        yield str(ch), str(sym), int(ts), bytes(best_bytes)
        return

    # 4) recurse
    for _, val, is_rep in iter_message_fields(obj):
        if isinstance(val, (bytes, bytearray)):
            continue
        if hasattr(val, "ParseFromString"):
            yield from extract_frames(val, debug_cb=debug_cb)
            return
        if is_rep and isinstance(val, (list, tuple)):
            for item in val:
                if hasattr(item, "ParseFromString"):
                    yield from extract_frames(item, debug_cb=debug_cb)
                    return
