"""
Configuration for ML Data Collector

Reads everything from .env (local PostgreSQL — same DB as backend/researcher).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

# Database — local PostgreSQL. No SQLite fallback: DB_PATH is gone.
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy ml_collector/.env.example to .env and "
        "point it at postgresql://mexc:<password>@127.0.0.1:5432/trading_bot"
    )

# Default: top 5 performing symbols (based on trade analysis)
#   LINKUSDT 94.6% TP | NEARUSDT 88.0% | VETUSDT 85.7% | AVAXUSDT 77.4% | ALGOUSDT 75.0%
_DEFAULT_SYMBOLS = 'LINKUSDT,NEARUSDT,VETUSDT,AVAXUSDT,ALGOUSDT'
SYMBOLS = [s.strip() for s in os.getenv('SYMBOLS', _DEFAULT_SYMBOLS).split(',') if s.strip()]

# Collection interval (seconds)
COLLECTION_INTERVAL = int(os.getenv('COLLECTION_INTERVAL', '10'))

# Scanner API configuration
SCANNER_BASE_URL = os.getenv('SCANNER_BASE_URL', 'http://127.0.0.1:8000/api/scanner/mexc/top')
SCANNER_TIMEOUT = int(os.getenv('SCANNER_TIMEOUT', '10'))

# Backend health endpoint (derived from scanner host)
BACKEND_HEALTH_URL = os.getenv('BACKEND_HEALTH_URL', 'http://127.0.0.1:8000/api/healthz')

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
