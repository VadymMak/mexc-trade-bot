"""
Validation test logger.
Records metrics every 5 minutes for analysis.
"""
import requests
import time
import csv
import os
from datetime import datetime
from typing import Dict, Any

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
LOG_FILE = "validation_log.csv"

def fetch_data() -> Dict[str, Any]:
    """Fetch all relevant data."""
    try:
        positions = requests.get(f"{BASE_URL}/api/exec/positions", timeout=5).json()
        pnl = requests.get(f"{BASE_URL}/api/pnl/summary?period=today", timeout=5).json()
        fees = requests.get(f"{BASE_URL}/api/pnl/fees?period=today", timeout=5).json()
        health = requests.get(f"{BASE_URL}/api/healthz", timeout=5).json()
        
        return {
            "positions": positions,
            "pnl": pnl,
            "fees": fees,
            "health": health,
        }
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def calculate_metrics(data: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate key metrics."""
    positions = data.get("positions", [])
    pnl = data.get("pnl", {})
    fees = data.get("fees", {})
    health = data.get("health", {})
    
    # Open positions
    open_positions = sum(1 for p in positions if p.get("qty", 0) != 0)
    
    # Total exposure
    total_exposure = sum(
        abs(p.get("qty", 0) * p.get("avg_price", 0))
        for p in positions
    )
    
    # P&L
    gross_pnl = pnl.get("total_usd", 0.0)
    total_fees = fees.get("total_fee_usd", 0.0)
    net_pnl = gross_pnl - total_fees
    
    # Fills
    total_fills = fees.get("count", 0)
    
    # Avg profit per fill
    avg_profit = net_pnl / total_fills if total_fills > 0 else 0.0
    
    # Cache hit rate
    cache_hit_rate = health.get("candles_cache_hit_rate", 0.0)
    
    return {
        "timestamp": datetime.now().isoformat(),
        "open_positions": open_positions,
        "total_exposure_usd": total_exposure,
        "gross_pnl_usd": gross_pnl,
        "total_fees_usd": total_fees,
        "net_pnl_usd": net_pnl,
        "total_fills": total_fills,
        "avg_profit_per_fill": avg_profit,
        "cache_hit_rate": cache_hit_rate,
    }

def log_metrics(metrics: Dict[str, Any]):
    """Log metrics to CSV."""
    file_exists = os.path.exists(LOG_FILE)
    
    with open(LOG_FILE, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=metrics.keys())
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow(metrics)
    
    print(f"[{metrics['timestamp']}] Logged: Net P&L=${metrics['net_pnl_usd']:.4f}, Fills={metrics['total_fills']}")

def main():
    """Main logging loop."""
    print(f"Starting validation logger...")
    print(f"API Base URL: {BASE_URL}")
    print(f"Log file: {LOG_FILE}")
    print(f"Interval: 5 minutes")
    print()
    
    try:
        while True:
            data = fetch_data()
            if data:
                metrics = calculate_metrics(data)
                log_metrics(metrics)
            
            time.sleep(300)  # 5 minutes
    except KeyboardInterrupt:
        print("\n\nLogging stopped by user.")

if __name__ == "__main__":
    main()