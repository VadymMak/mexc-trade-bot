# app/market_data/mexc_pb/__init__.py
"""
Пакет с автогенерированными protobuf-модулями MEXC Spot v3.

Задачи:
- Поддержать абсолютные импорты внутри *_pb2.py (например: `import PublicDealsV3Api_pb2`).
- Сначала импортировать все базовые *_pb2, затем — обёртку PushDataV3ApiWrapper_pb2.
- Не требовать ручного обновления списка файлов: autodiscovery по каталогу.

Результат:
- Все *_pb2 доступны как app.market_data.mexc_pb.<Имя> и по короткому алиасу в sys.modules.
- Функция list_loaded() возвращает имена успешно загруженных модулей.
"""
from __future__ import annotations

import os
import sys
from importlib import import_module
from typing import List

__all__: List[str] = []

# ── Добавляем путь каталога в sys.path, чтобы абсолютные импорты работали ──
_pkg_dir = os.path.dirname(__file__)
if _pkg_dir and _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

def _alias_in_sysmodules(name: str, mod) -> None:
    """
    Кладём модуль под коротким именем (без пакета), чтобы строки из *_pb2.py вида
      `import PublicDealsV3Api_pb2 as PublicDealsV3Api__pb2`
    резолвились к нашим локальным файлам.
    """
    try:
        # Не перетираем, если уже есть
        if name not in sys.modules:
            sys.modules[name] = mod
    except Exception:
        pass

def _safe_import_local(name: str) -> bool:
    """
    Импортирует .<name> из текущего пакета, добавляет в globals/__all__,
    и создаёт алиас в sys.modules по короткому имени.
    """
    try:
        mod = import_module(f".{name}", __name__)
        globals()[name] = mod
        if name not in __all__:
            __all__.append(name)
        _alias_in_sysmodules(name, mod)
        return True
    except Exception:
        return False

def _discover_pb2_files() -> List[str]:
    """
    Возвращает список имён модулей без .py: ['PublicDealsV3Api_pb2', ...]
    """
    try:
        files = os.listdir(_pkg_dir)
    except Exception:
        return []
    out: List[str] = []
    for f in files:
        if not f.endswith("_pb2.py"):
            continue
        if f.startswith("_"):
            continue
        out.append(f[:-3])  # убрать .py
    return out

# ── Автообнаружение всех *_pb2 модулей ─────────────────────────────────────
_all_pb2 = _discover_pb2_files()

# 1) Определим wrapper и базовые:
WRAPPER = "PushDataV3ApiWrapper_pb2"
bases = [n for n in _all_pb2 if n != WRAPPER]

# 2) Сначала загрузим базовые (чтобы абсолютные импорты внутри них и в обёртке сработали)
_loaded_bases: List[str] = []
for n in sorted(bases):
    if _safe_import_local(n):
        _loaded_bases.append(n)

# 3) Затем обязательно подгружаем обёртку, если она присутствует
_loaded_wrapper = _safe_import_local(WRAPPER) if WRAPPER in _all_pb2 else False

def list_loaded() -> List[str]:
    """Вернуть список успешно загруженных *_pb2 модулей (в порядке импорта)."""
    out = list(_loaded_bases)
    if _loaded_wrapper:
        out.append(WRAPPER)
    return out
