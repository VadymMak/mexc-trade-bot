# ML Data Collector

Standalone script for collecting ML training data from MEXC exchange.

## Features

- WebSocket connection to MEXC
- Tracks order books for specified symbols
- Writes snapshots to `ml_snapshots` table
- Auto-reconnect on connection loss
- Runs independently from main backend

## Setup
```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows Git Bash)
source .venv/Scripts/activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure .env
cp .env.example .env
# Edit .env with your settings
```

## Usage
```bash
# Activate venv
source .venv/Scripts/activate

# Run collector
python collector.py
```

## Configuration

Edit `.env` file:
```env
SYMBOLS=WLFIUSDT,UCNUSDT,HBARUSDT
DB_PATH=../backend/mexc.db
COLLECTION_INTERVAL=2
```

## Monitoring

- Logs written to `collector.log`
- Console shows connection status
- Records count every 100 writes