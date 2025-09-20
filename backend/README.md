mexc-trade-bot/
├── app/
│   ├── __init__.py
│   ├── main.py                # Запуск FastAPI-приложения
│
│   ├── config/                # Настройки и переменные окружения
│   │   ├── __init__.py
│   │   ├── settings.py
│   │   └── constants.py
│
│   ├── models/                # SQLAlchemy модели (позиции, ордера, сессии)
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── orders.py
│   │   ├── positions.py
│   │   ├── sessions.py
│   │   └── fills.py
│
│   ├── db/                    # Инициализация базы данных
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── session.py
│
│   ├── market_data/          # Работа с WebSocket и ценами
│   │   ├── __init__.py
│   │   ├── ws_client.py       # Подключение к MEXC Public WS
│   │   └── book_tracker.py    # Поддержка стакана и цен
│
│   ├── strategy/             # Логика стратегии
│   │   ├── __init__.py
│   │   ├── engine.py          # Цикл стратегия: покупка → продажа
│   │   ├── edge_calc.py       # Расчёт edge
│   │   └── risk.py            # Ограничения (лимиты, таймауты, P&L)
│
│   ├── execution/            # Торговые действия (live / paper)
│   │   ├── __init__.py
│   │   ├── router.py          # API FastAPI: start/stop bot
│   │   ├── paper_executor.py  # Симуляция сделок (Paper Mode)
│   │   └── live_executor.py   # Реальные сделки через MEXC API
│
│   ├── services/             # Общие сервисы и утилиты
│   │   ├── __init__.py
│   │   ├── session_manager.py # Управление сессиями и состояниями
│   │   ├── metrics.py         # P&L, edge, отчёты
│   │   └── logger.py
│
│   ├── api/                  # FastAPI роутеры
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   └── stream.py          # SSE или WebSocket статус
│
│   └── utils/
│       ├── __init__.py
│       ├── enums.py
│       └── time.py
│
├── tests/
│   ├── test_strategy.py
│   ├── test_market_data.py
│   └── ...
│
├── .env                      # Переменные окружения (ключи, режим paper/live)
├── requirements.txt
├── docker-compose.yml        # Позже: для развёртывания
└── README.md
