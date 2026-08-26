# VPS Migration Plan — mexc-trade-bot

> Historical as of 2026-08-26 — superseded by BLUEPRINT.md.
# Цель: переехать с Railway ($12/мес) на Hetzner VPS ($4/мес)
# Paper trader НЕ трогаем — работает на Railway пока не подтвердим VPS

## СТАТУС
- [ ] Шаг 1: Купить и настроить VPS
- [ ] Шаг 2: Развернуть researcher-paper на VPS (параллельно Railway)
- [ ] Шаг 3: Подтвердить работу VPS — 24 часа наблюдения
- [ ] Шаг 4: Остановить researcher-paper на Railway
- [ ] Шаг 5: Настроить researcher-live на VPS (готов но не активирован)
- [ ] Шаг 6: Создать Neon brain-vectors проект
- [ ] Шаг 7: Мигрировать pgvector данные Railway → Neon
- [ ] Шаг 8: Удалить Railway backend + PostgreSQL
- [ ] Шаг 9: Live trading тест с $5 позициями
- [ ] Шаг 10: Поднять до $10 base — полный live

---

## ШАГ 1: Купить Hetzner VPS

1. Зайди на https://hetzner.com/cloud
2. Создай аккаунт
3. Выбери: **CX22** (2 vCPU, 4GB RAM, 40GB SSD) — €3.79/мес
   - Region: **Nuremberg** или **Helsinki** (ближе к Gate/MEXC EU серверам)
   - OS: **Ubuntu 22.04 LTS**
   - SSH Key: добавь свой публичный ключ
4. Создай сервер — получишь IP адрес

**Почему CX22 а не CX11:**
- Researcher потребляет 500MB-1GB RAM
- С paper + live двумя процессами нужно 4GB
- CX22 = €3.79 vs CX11 = €3.29 — разница €0.50 но памяти вдвое больше

---

## ШАГ 2: Первоначальная настройка VPS

```bash
# Подключись к серверу
ssh root@YOUR_VPS_IP

# Запусти setup скрипт (файл vps-setup.sh в этом репо)
curl -o setup.sh https://raw.githubusercontent.com/VadymMak/mexc-trade-bot/main/vps-setup.sh
chmod +x setup.sh
./setup.sh
```

Или вручную — следуй инструкциям в `vps-setup.sh`

---

## ШАГ 3: Развернуть researcher-paper на VPS

```bash
# На VPS (уже как пользователь bot):
cd /home/bot

# Клонируй репо
git clone https://github.com/VadymMak/mexc-trade-bot.git
cd mexc-trade-bot

# Создай .env для paper trader
cp researcher/.env.example researcher/.env.paper
nano researcher/.env.paper
# Заполни: NEON_DATABASE_URL, LIVE_TRADING=false, и т.д.

# Установи зависимости
pip install -r researcher/requirements.txt

# Запусти через PM2
pm2 start ecosystem.config.js --only researcher-paper
pm2 save
pm2 logs researcher-paper
```

**ВАЖНО:** На этом этапе Railway researcher-paper ПРОДОЛЖАЕТ работать.
Оба бота пишут в одну NeonDB — это нормально, paper_positions будет общей.
Наблюдай логи VPS 24 часа. Если всё ок — останавливаешь Railway версию.

---

## ШАГ 4: Остановить Railway researcher

Только после 24ч наблюдения что VPS версия стабильна:
1. Railway Dashboard → mexc-trade-bot → researcher сервис
2. Settings → **Suspend Service**
3. Подожди 5 минут — убедись что VPS продолжает торговать

---

## ШАГ 5: Настроить researcher-live (не активировать)

```bash
# На VPS:
cp researcher/.env.paper researcher/.env.live
nano researcher/.env.live
# Измени:
# LIVE_TRADING=true
# STARTING_EQUITY_USDT=600
# COMPOUND_ENABLED=true
# GATE_FUTURES_API_KEY=ваш_ключ
# GATE_FUTURES_SECRET=ваш_секрет
# MEXC_FUTURES_API_KEY=ваш_ключ
# MEXC_FUTURES_SECRET=ваш_секрет

# PM2 процесс создан но ОСТАНОВЛЕН
pm2 start ecosystem.config.js --only researcher-live
pm2 stop researcher-live  # ← остановили, готов к запуску когда нужно
```

---

## ШАГ 6: Создать Neon brain-vectors

1. Зайди на https://neon.tech
2. Create new project: **brain-vectors**
3. Region: US East (тот же что arb-researcher)
4. Запусти schema:

```bash
# На VPS или локально:
psql BRAIN_NEON_URL < brain/schema.sql
```

---

## ШАГ 7: Мигрировать pgvector Railway → Neon

```bash
# На VPS:
cd /home/bot/mexc-trade-bot
python brain/migrate.py \
  --source "postgresql://..." \  # Railway PostgreSQL URL
  --target "postgresql://..."    # Neon brain-vectors URL
```

Потом обнови backend env:
```
BRAIN_DATABASE_URL=postgresql://...neon.tech/brain_vectors
```

---

## ШАГ 8: Удалить Railway сервисы

После подтверждения что brain на Neon работает:
1. Railway → gallant-youthfulness → **Delete Service**
2. Railway → pgvector-volume → **Delete Volume**
3. Railway → backend → **Delete Service** (если frontend не зависит)

**Экономия: -$8-10/мес**

---

## ШАГ 9-10: Live trading

Через 2-3 недели когда вернёшься:
1. Проверь paper trader статистику через аналайзер
2. Если WR >85% и стабильные данные → запускай live:

```bash
pm2 start researcher-live
pm2 logs researcher-live
```

3. Первую неделю live: наблюдай логи, позиции $5-10
4. Если всё нормально → поднимай до $10 base + compounding

---

## МОНИТОРИНГ ПОКА ТЫ В ОТЪЕЗДЕ

### Telegram алёрты (уже есть в backend)
Убедись что Telegram уведомления настроены на VPS тоже.

### Простой health check через cron:
```bash
# На VPS добавь в crontab:
*/5 * * * * pm2 list | grep researcher-paper | grep -q online || pm2 restart researcher-paper
```

### Просмотр логов удалённо:
```bash
ssh bot@YOUR_VPS_IP 'pm2 logs researcher-paper --lines 50'
```

---

## РАСХОДЫ ПОСЛЕ МИГРАЦИИ

| Сервис | До | После |
|--------|-----|-------|
| Railway | $12.17 | $0 |
| Hetzner VPS | $0 | $4.20 |
| Neon arb-researcher | $0 | $0 (free) |
| Neon brain-vectors | $0 | $0 (free) |
| Vercel frontend | $0 | $0 |
| **ИТОГО** | **$12.17** | **$4.20** |

**Экономия: $8/месяц = $96/год**

---

## ROLLBACK ПЛАН

Если что-то пошло не так на VPS:
1. Railway researcher → **Resume Service** (2 клика)
2. VPS останавливаешь: `pm2 stop researcher-paper`
3. Разбираешься в проблеме спокойно
4. Railway продолжает торговать пока чинишь VPS

Нет риска потерять работающий бот.
