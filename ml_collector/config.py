"""
Configuration for ML Data Collector
"""
from pathlib import Path

# Top 5 performing symbols (based on trade analysis)
SYMBOLS = [
    'LINKUSDT',  # 94.6% TP - Excellent
    'NEARUSDT',  # 88.0% TP - Good
    'VETUSDT',   # 85.7% TP - Good
    'AVAXUSDT',  # 77.4% TP - Acceptable
    'ALGOUSDT',  # 75.0% TP - Acceptable
]

# Database path (relative to collector directory)
DB_PATH = Path(__file__).parent.parent / 'backend' / 'mexc.db'

# Collection interval (seconds)
COLLECTION_INTERVAL = 10  # Increased from 5s to reduce load

# Scanner API configuration
SCANNER_BASE_URL = "http://localhost:8000/api/scanner/mexc/top"
SCANNER_TIMEOUT = 10  # Increased from 5s to 10s