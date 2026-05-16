#!/bin/bash
# VPS Setup Script — mexc-trade-bot
# Ubuntu 22.04 LTS
# Запускать от root: bash vps-setup.sh

set -e
echo "=== mexc-trade-bot VPS Setup ==="

# ── 1. Обновление системы ─────────────────────────────────────────────────────
echo "[1/7] Updating system..."
apt-get update -q && apt-get upgrade -y -q

# ── 2. Базовые пакеты ─────────────────────────────────────────────────────────
echo "[2/7] Installing base packages..."
apt-get install -y -q \
    git curl wget nano htop \
    python3.11 python3.11-venv python3-pip \
    build-essential libssl-dev libffi-dev \
    postgresql-client \
    ufw fail2ban

# ── 3. Node.js + PM2 ──────────────────────────────────────────────────────────
echo "[3/7] Installing Node.js + PM2..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
npm install -g pm2
pm2 startup systemd -u root --hp /root

# ── 4. Создать пользователя bot ───────────────────────────────────────────────
echo "[4/7] Creating bot user..."
useradd -m -s /bin/bash bot || echo "User bot already exists"
mkdir -p /home/bot/.ssh
cp /root/.ssh/authorized_keys /home/bot/.ssh/ 2>/dev/null || true
chown -R bot:bot /home/bot/.ssh
chmod 700 /home/bot/.ssh
chmod 600 /home/bot/.ssh/authorized_keys 2>/dev/null || true

# ── 5. Firewall ───────────────────────────────────────────────────────────────
echo "[5/7] Configuring firewall..."
ufw allow OpenSSH
ufw allow 22/tcp
ufw --force enable

# ── 6. Python venv для бота ───────────────────────────────────────────────────
echo "[6/7] Setting up Python environment..."
su - bot -c "python3.11 -m venv /home/bot/venv"
su - bot -c "/home/bot/venv/bin/pip install --upgrade pip wheel"

# ── 7. Клонировать репо ───────────────────────────────────────────────────────
echo "[7/7] Cloning repository..."
su - bot -c "git clone https://github.com/VadymMak/mexc-trade-bot.git /home/bot/mexc-trade-bot"
su - bot -c "/home/bot/venv/bin/pip install -r /home/bot/mexc-trade-bot/researcher/requirements.txt"

# ── Финал ─────────────────────────────────────────────────────────────────────
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Следующие шаги:"
echo "1. Создай env файлы:"
echo "   cp /home/bot/mexc-trade-bot/researcher/.env.example /home/bot/mexc-trade-bot/researcher/.env.paper"
echo "   nano /home/bot/mexc-trade-bot/researcher/.env.paper"
echo ""
echo "2. Запусти paper trader:"
echo "   su - bot"
echo "   cd mexc-trade-bot"
echo "   pm2 start ecosystem.config.js --only researcher-paper"
echo "   pm2 save"
echo ""
echo "3. Проверь логи:"
echo "   pm2 logs researcher-paper"
