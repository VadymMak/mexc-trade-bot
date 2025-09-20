# app/market_data/helpers/proto_utils.py
from __future__ import annotations
from google.protobuf.json_format import MessageToDict

import gzip
from typing import Any, Callable, Iterator, Optional, Tuple, List

# ────────────── small helpers ──────────────
FIELD_NAMES_CHANNEL = ("channel", "topic", "ch")
FIELD_NAMES_SYMBOL = ("symbol", "instId", "s")
FIELD_NAMES_SENDTS = ("sendTime", "sendtime", "ts", "time", "t")
FIELD_NAMES_DATA = ("data", "payload", "d")


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


def iter_message_fields(msg: Any) -> Iterator[Tuple[str, Any, bool]]:
    desc = getattr(msg, "DESCRIPTOR", None)
    if not desc:
        return
    for f in desc.fields:
        try:
            val = getattr(msg, f.name)
        except Exception:
            continue
        yield f.name, val, f.label == f.LABEL_REPEATED


def hexdump(b: bytes, n: int = 48) -> str:
    return " ".join(f"{x:02X}" for x in b[:n])


def first_set_fields_dict(msg) -> dict:
    out = {}
    for fd, v in msg.ListFields():
        if hasattr(v, "DESCRIPTOR"):
            try:
                out[fd.name] = MessageToDict(
                    v,
                    including_default_value_fields=False,
                    preserving_proto_field_name=True,
                )
            except Exception:
                out[fd.name] = str(v)
        else:
            out[fd.name] = v
    return out


def collect_bytes_candidates(obj: Any, prefix: str = "") -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    try:
        for name, val, is_rep in iter_message_fields(obj):
            path = f"{prefix}.{name}" if prefix else name
            if isinstance(val, (bytes, bytearray)):
                out.append((path, bytes(val)))
                continue
            if hasattr(val, "DESCRIPTOR"):
                out.extend(collect_bytes_candidates(val, path))
            elif is_rep and isinstance(val, (list, tuple)):
                for idx, item in enumerate(val):
                    if hasattr(item, "DESCRIPTOR"):
                        out.extend(collect_bytes_candidates(item, f"{path}[{idx}]"))
    except Exception:
        pass
    return out


def extract_frames(
    obj: Any,
    debug_cb: Optional[Callable[[str], None]] = None,
) -> Iterator[Tuple[str, str, int, bytes]]:
    """
    Yield zero or more frames as (channel, symbol, send_ts, payload_bytes)
    from a generic wrapper message.
    """
    ch = get_attr_any(obj, FIELD_NAMES_CHANNEL, "") or ""
    sym = get_attr_any(obj, FIELD_NAMES_SYMBOL, "") or ""
    ts = get_attr_any(obj, FIELD_NAMES_SENDTS, 0) or 0

    # 1) any non-empty submessage? serialize it
    try:
        yielded = False
        for name, val, _ in iter_message_fields(obj):
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
    data = get_attr_any(obj, FIELD_NAMES_DATA, None)
    if isinstance(data, (bytes, bytearray)) and len(data) > 0:
        yield str(ch), str(sym), int(ts), bytes(data)
        return

    # 3) nested bytes, choose best candidate
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

    # 4) deep recurse into submessages
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


def debug_envelope_shape(obj: Any) -> None:
    try:
        names = [f[0] for f in iter_message_fields(obj)]
        print("🧩 Envelope fields:", names)
        for name, val, is_rep in iter_message_fields(obj):
            if hasattr(val, "DESCRIPTOR"):
                sub = [f[0] for f in iter_message_fields(val)]
                print(f"   ▸ {name} :: message (rep={is_rep}) → {sub}")
            elif is_rep and isinstance(val, (list, tuple)) and val and hasattr(val[0], "DESCRIPTOR"):
                sub = [f[0] for f in iter_message_fields(val[0])]
                print(f"   ▸ {name} :: repeated message → {sub}")
    except Exception:
        pass


def _message_has_fields(cls: type, required: set[str]) -> bool:
    try:
        d = getattr(cls, "DESCRIPTOR", None)
        if not d:
            return False
        fields = {f.name.lower() for f in d.fields}
        return required.issubset(fields)
    except Exception:
        return False


def find_book_ticker_cls(mod) -> Optional[type]:
    try:
        d = getattr(mod, "DESCRIPTOR", None)
        if not d:
            return None

        scalar_req = {"bidprice", "bid_quantity", "bidquantity", "askprice", "ask_quantity", "askquantity"}
        price_qty = {"price", "quantity"}

        chosen = None

        # scalar types
        for typ in d.message_types_by_name.values():
            cls = getattr(mod, typ.name, None)
            if cls is None:
                continue
            if _message_has_fields(cls, {"bidprice", "askprice"} | ({"bidquantity"} if "bidquantity" in scalar_req else set()) | ({"askquantity"} if "askquantity" in scalar_req else set())):
                chosen = cls
                break
            if _message_has_fields(cls, {"bid_price", "ask_price", "bid_quantity", "ask_quantity"}):
                chosen = cls
                break

        # repeated types (bids[]/asks[])
        if chosen is None:
            for typ in d.message_types_by_name.values():
                cls = getattr(mod, typ.name, None)
                if cls is None:
                    continue
                msg = cls()
                rep_msgs = []
                for fname, _, is_rep in iter_message_fields(msg):
                    if not is_rep:
                        continue
                    field_desc = msg.DESCRIPTOR.fields_by_name.get(fname)
                    if field_desc and field_desc.message_type:
                        elem_desc = field_desc.message_type
                        elem_fields = {f.name for f in elem_desc.fields}
                        if price_qty.issubset(elem_fields):
                            rep_msgs.append(fname)
                if len(rep_msgs) >= 2:
                    chosen = cls
                    break

        if chosen is not None:
            print(f"ℹ️ Using book-ticker message in {mod.__name__}: {chosen.__name__}")
            return chosen

    except Exception as e:
        print(f"🟡 find_book_ticker_cls error in {getattr(mod,'__name__','?')}: {e}")
    return None


def find_depth_cls(mod) -> Optional[type]:
    try:
        d = getattr(mod, "DESCRIPTOR", None)
        if not d:
            return None
        for typ in d.message_types_by_name.values():
            cls = getattr(mod, typ.name, None)
            if cls is None:
                continue
            msg = cls()
            rep_ok = 0
            for fname, _, is_rep in iter_message_fields(msg):
                if not is_rep:
                    continue
                field_desc = msg.DESCRIPTOR.fields_by_name.get(fname)
                if field_desc and field_desc.message_type:
                    elem_desc = field_desc.message_type
                    elem_fields = {f.name for f in elem_desc.fields}
                    if {"price", "quantity"}.issubset(elem_fields):
                        rep_ok += 1
            if rep_ok >= 2:
                print(f"ℹ️ Using depth message: {typ.name}")
                return cls
    except Exception as e:
        print(f"🟡 find_depth_cls error: {e}")
    return None


def bruteforce_decode_book(msg_bytes: bytes, modules: list[Any]):
    candidates = []
    for mod in modules:
        if not mod:
            continue
        try:
            for typ_name, _ in mod.DESCRIPTOR.message_types_by_name.items():
                cls = getattr(mod, typ_name, None)
                if not cls:
                    continue
                m = cls()
                try:
                    m.ParseFromString(msg_bytes)
                except Exception:
                    continue
                if m.ListFields():
                    candidates.append((mod.__name__, typ_name, m))
        except Exception:
            continue

    def score(m):
        names = {fd.name.lower() for fd, _ in m.ListFields()}
        s = 0
        for needle in ("bid", "ask"):
            if any(needle in n for n in names):
                s += 1
        for needle in ("price", "quantity", "qty"):
            if any(needle in n for n in names):
                s += 1
        return s

    if not candidates:
        return None
    best = max(candidates, key=lambda t: score(t[2]))
    return best  # (module_name, type_name, parsed_message)
