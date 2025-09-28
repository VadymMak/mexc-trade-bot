# app/models/__init__.py
"""
SQLAlchemy models registry.

Импортируем модули моделей, чтобы они зарегистрировались в Base.metadata.
Не тянем конкретные имена классов (Order/Position/...), чтобы избежать ImportError,
если где-то имена отличаются.
"""

from app.models.base import Base  # базовый класс декларативных моделей

# Базовые модели (best-effort)
try:
    import app.models.orders  # noqa: F401
except Exception:
    pass

try:
    import app.models.positions  # noqa: F401
except Exception:
    pass

try:
    import app.models.fills  # noqa: F401
except Exception:
    pass

try:
    import app.models.sessions  # noqa: F401
except Exception:
    pass

# Новые модели состояния UI/стратегий (обязательные)
import app.models.ui_state  # noqa: F401
import app.models.strategy_state  # noqa: F401

# ─────────────── PnL tables (новые) ───────────────
# Регистрируем леджер и агрегаты, чтобы Base.metadata знала о таблицах
try:
    import app.models.pnl_ledger  # noqa: F401
except Exception:
    pass

try:
    import app.models.pnl_daily  # noqa: F401
except Exception:
    pass

__all__ = ["Base"]
