# app/models/__init__.py

"""
SQLAlchemy models registry.

Импортируем модули моделей, чтобы они зарегистрировались в Base.metadata.
Не тянем конкретные имена классов (Order/Position/...), чтобы избежать ImportError,
если где-то имена отличаются.
"""

from app.models.base import Base  # базовый класс декларативных моделей

# Импортируем сами модули, без конкретных имён классов
# (если какого-то модуля нет — можно опционально залогировать, но не падать)
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

# Новые модели состояния UI/стратегий
# Эти файлы у нас точно существуют — импортируем обязательно.
import app.models.ui_state  # noqa: F401
import app.models.strategy_state  # noqa: F401
