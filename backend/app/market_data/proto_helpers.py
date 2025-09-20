from __future__ import annotations
from typing import Any, Iterator, Tuple, Optional, Callable, List
from google.protobuf.json_format import MessageToDict

# ——— Generic proto field helpers ———
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

def first_set_fields_dict(msg: Any) -> dict:
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

# ——— Class discovery ———
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
        chosen_kind = ""

        # scalar
        for typ in d.message_types_by_name.values():
            cls = getattr(mod, typ.name, None)
            if cls is None:
                continue
            if _message_has_fields(cls, {"bidprice", "askprice"} | ({"bidquantity"} if "bidquantity" in scalar_req else set()) | ({"askquantity"} if "askquantity" in scalar_req else set())):
                chosen = cls; chosen_kind = "scalar"; break
            if _message_has_fields(cls, {"bid_price", "ask_price", "bid_quantity", "ask_quantity"}):
                chosen = cls; chosen_kind = "scalar"; break

        # repeated
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
                    chosen = cls; chosen_kind = "repeated"; break

        if chosen is not None:
            print(f"ℹ️ Using book-ticker message in {mod.__name__}: {chosen.__name__} ({chosen_kind})")
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

# ——— Brute-force decode for book ticker ———
def bruteforce_decode_book(msg_bytes: bytes, mods: list) -> Optional[tuple[str, str, Any]]:
    candidates = []
    for mod in mods:
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

    if not candidates:
        return None

    def score(m):
        names = {fd.name.lower() for fd, _ in m.ListFields()}
        s = 0
        for needle in ("bid", "ask"):
            if any(needle in n for n in names): s += 1
        for needle in ("price", "quantity", "qty"):
            if any(needle in n for n in names): s += 1
        return s

    return max(candidates, key=lambda t: score(t[2]))
