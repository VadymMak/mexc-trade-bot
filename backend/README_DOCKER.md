# 🐳 Docker Deployment Guide

## Quick Start (Local Testing)

### 1. Build and Run with Docker Compose
```bash
# Copy environment template
cp .env.example .env

# Edit .env with your settings
nano .env

# Build and start
docker-compose up -d

# Check logs
docker-compose logs -f

# Check health
curl http://localhost:8000/api/healthz
```

### 2. Stop
```bash
docker-compose down
```

---

## Production Deployment

### Option A: Docker Compose (Simple)
```bash
# 1. Copy production env
cp .env.production .env

# 2. Edit with REAL API keys
nano .env

# 3. Start
docker-compose up -d

# 4. Monitor
docker-compose logs -f backend
```

### Option B: Plain Docker
```bash
# Build
docker build -t mexc-bot:latest .

# Run
docker run -d \
  --name mexc-bot \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --env-file .env.production \
  --restart unless-stopped \
  mexc-bot:latest

# Check logs
docker logs -f mexc-bot
```

---

## AWS Deployment

### AWS Lightsail (Easiest - $10/month)
```bash
# 1. Build locally
docker build -t mexc-bot:latest .

# 2. Push to Lightsail
aws lightsail push-container-image \
  --service-name mexc-bot \
  --label latest \
  --image mexc-bot:latest

# 3. Create service (first time only)
aws lightsail create-container-service \
  --service-name mexc-bot \
  --power small \
  --scale 1

# 4. Deploy
aws lightsail create-container-service-deployment \
  --service-name mexc-bot \
  --containers file://lightsail-containers.json
```

### AWS EC2
```bash
# 1. SSH to EC2
ssh -i your-key.pem ubuntu@your-ec2-ip

# 2. Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 3. Clone repo
git clone https://github.com/your-repo/mexc-trade-bot
cd mexc-trade-bot/backend

# 4. Setup .env
nano .env.production

# 5. Run
docker-compose up -d

# 6. Enable auto-start on reboot
sudo systemctl enable docker
```

---

## Maintenance

### View Logs
```bash
docker-compose logs -f
docker-compose logs -f --tail=100
```

### Restart
```bash
docker-compose restart
```

### Update Code
```bash
git pull
docker-compose build
docker-compose up -d
```

### Backup Database
```bash
docker-compose exec backend tar czf /tmp/backup.tar.gz /app/data
docker cp mexc-bot-backend:/tmp/backup.tar.gz ./backup-$(date +%Y%m%d).tar.gz
```

### Clean Up
```bash
docker-compose down -v  # Remove volumes too
docker system prune -a  # Clean all unused images
```

---

## Troubleshooting

### Container won't start
```bash
docker-compose logs backend
```

### Health check failing
```bash
docker-compose exec backend curl http://localhost:8000/api/healthz
```

### Database issues
```bash
docker-compose exec backend ls -la /app/data
```

### Reset everything
```bash
docker-compose down -v
rm -rf data/* logs/*
docker-compose up -d