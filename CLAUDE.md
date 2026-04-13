# CLAUDE.md — mexc-trade-bot (Arb Research + Live Trading)

## ⚠️ ОБЯЗАТЕЛЬНО при каждом запуске

### 1. Загрузить контекст из brain
```
mcp__multi-ai-chat__build_context_for_query(project_id="29", query="<текущая задача>")
```
Или если нужна история решений:
```
mcp__multi-ai-chat__get_session_summaries(project_id=29, limit=5)
```
Brain содержит: баги и фиксы, архитектурные решения, результаты датасетов, параметры стратегии.

### 2. Прочитать нужные файлы перед кодом
Ключевые файлы: `researcher/app/config.py`, `researcher/app/core/paper_trader.py`,
`researcher/app/core/simulator.py`, `researcher/app/live/arb_executor.py`

### 3. После каждого бага/решения — сохранить в brain
```
mcp__multi-ai-chat__save_session_summary(project_id=29, content="...", topics=[...])
```

---

## Проект
Cross-exchange арбитраж на фьючерсах Gate.io + MEXC (+ KuCoin как feed).
Исследовательский сервис (researcher/) собирает данные, paper-торгует, готов к live.

## Стек
Python 3.11, asyncio, aiohttp, asyncpg (Neon DB), Railway (deploy).

## Команды
```bash
cd researcher && python -m app.main    # запуск researcher
pnpm dev                               # frontend (Next.js)
```

## Архитектура

### Режимы торговли
- `LIVE_TRADING=false` (default) → `PaperTrader` — симуляция, данные в БД
- `LIVE_TRADING=true` → `ArbLiveExecutor` — реальные ордера на MEXC Futures + Gate Futures

### Ключевые компоненты
```
researcher/app/
├── config.py              — все параметры стратегии (MIN_SPREAD_PCT, MM_TIER*, etc.)
├── core/
│   ├── spread_matrix.py   — вычисляет zscore по всем парам
│   ├── paper_trader.py    — paper trading (on_spread callback)
│   └── simulator.py       — расчёт P&L с комиссиями и slippage
├── live/                  — LIVE trading (drop-in для paper_trader)
│   ├── base.py            — FuturesClient Protocol
│   ├── mexc_futures.py    — MEXC Contract API
│   ├── gate_futures.py    — Gate Futures API
│   └── arb_executor.py    — двухплечовый executor + rollback
├── collectors/            — WebSocket коллекторы цен (Binance, Bybit, Gate, MEXC, KuCoin)
└── db/neon_db.py          — PostgreSQL (Neon)
```

### Обмены
| Exchange | Роль | API |
|---|---|---|
| Gate | Trading (long/short) | /futures/usdt/ |
| MEXC | Trading (long/short) | contract.mexc.com |
| KuCoin | Price feed only | Нет сделок (только BTC/ETH) |
| Binance | Mark-price reference | НЕ торгуется |
| Bybit | Mark-price reference | НЕ торгуется |
| MEXC Spot | Basis analysis | REST polling 5s |

### Фильтры (phantom exchanges)
```python
_PHANTOM_EXCHANGES = frozenset({"binance", "bybit", "mexc_spot"})
```
В `backend/app/routers/arbitrage.py` — эти пары не попадают в frontend/trades.

## Текущие параметры стратегии

| Параметр | Значение | Обоснование |
|---|---|---|
| MIN_SPREAD_PCT | 0.005 (0.5%) | <0.5% — net negative (-$1.56, 53% WR) |
| ZSCORE_THRESHOLD | 2.5 | mean-reversion entry |
| MIN_SPREAD_CV | 1.0 | cv<1 = structural spread, не реверсирует |
| TAKE_PROFIT_RATIO | 0.50 | закрыть при 50% сужения спреда |
| STOP_LOSS_RATIO | 1.5 | 1.5× entry spread |
| PAPER_DEAL_SIZE_USDT | 10.0 | базовый размер (×1 до ×5 через MM тиры) |
| TRADING_EXCHANGES | gate,mexc,kucoin | только эти открывают сделки |

### Dynamic MM Sizing
| Spread | Множитель | Размер | Fee/Gross |
|---|---|---|---|
| <1.0% | ×1 | $10 | ~63% |
| ≥1.0% | ×2 | $20 | ~31% |
| ≥1.5% | ×3 | $30 | ~21% |
| ≥2.0% | ×5 | $50 | ~12% |
+ volume cap: min(size, max($10, trade_velocity×7.5×0.30))

**Симуляция:** flat $10 → net $53/день, dynamic → net $143/день (+169%)

## Результаты тестов

### Paper trading (2026-04-12 → 2026-04-13)
- ZSCORE 2796 trades, 72-74% WR, ~$53 net/день на $10 flat
- large_spread: ОТКЛЮЧЁН (27% WR, -$122/день — commit 8f48ad1)
- MIN_SPREAD 0.5%: ночной тест +$25.98 vs +$19.61 при 0.3%

### Exit breakdown (zscore)
- TAKE_PROFIT: 91% trades, +$55 PnL ✅
- STOP_LOSS: 3% trades, -$8 PnL
- ZSCORE_REVERT: 5% trades, -$1 PnL

### Топ символы
ARIA_USDT, AIOT_USDT, MAGMA_USDT, TRADOOR_USDT, AKE_USDT, BAS_USDT

## Live trading (готово, не активировано)
Для включения в Railway:
```
LIVE_TRADING=true
MEXC_FUTURES_API_KEY=...
MEXC_FUTURES_SECRET=...
GATE_FUTURES_API_KEY=...
GATE_FUTURES_SECRET=...
```
Депозит минимум: $300 MEXC + $300 Gate = $600 total.
Testnet сначала: MEXC → contract.mexcdevelop.com, Gate → testnet URL.

## Важные баги (зафиксированы в brain)
- Phantom spreads (Binance 160%+): фильтр в `internal_spread_update()` — commit 35662c0
- MEXC Spot WS reconnect 30s: заменён на REST polling 5s — commit 6376235
- mexc↔mexc_spot пары в frontend: mexc_spot добавлен в _PHANTOM_EXCHANGES — commit 98ea967
- large_spread mode уничтожал $122/день: отключён — commit 8f48ad1

## Язык общения
Отвечать на **русском**.
