"""Configuration for ML data collector."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Symbols to collect
SYMBOLS = os.getenv("SYMBOLS", "WLFIUSDT,UCNUSDT,HBARUSDT").split(",")

# Database path
DB_PATH = Path(os.getenv("DB_PATH", "../backend/mexc.db"))

# MEXC WebSocket URL
MEXC_WS_URL = os.getenv("MEXC_WS_URL", "wss://wbs.mexc.com/ws")

# Collection interval (seconds)
COLLECTION_INTERVAL = int(os.getenv("COLLECTION_INTERVAL", "2"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

print(f"[CONFIG] Symbols: {SYMBOLS}")
print(f"[CONFIG] DB Path: {DB_PATH}")
print(f"[CONFIG] Interval: {COLLECTION_INTERVAL}s")
