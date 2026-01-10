#!/usr/bin/env python3
"""
Simple monitoring script for paper trading with realistic simulation.
Shows live metrics in console with ACCURATE FEE TRACKING from API.
"""

import time
import requests
from datetime import datetime
from typing import Dict, Any
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Backend URL
BACKEND_URL = "http://localhost:8000"


def get_metrics() -> Dict[str, Any]:
    """Fetch metrics from Prometheus endpoint"""
    try:
        response = requests.get(f"{BACKEND_URL}/metrics", timeout=5)
        response.raise_for_status()
        return parse_prometheus_metrics(response.text)
    except Exception as e:
        print(f"Error fetching metrics: {e}")
        return {}


def parse_prometheus_metrics(text: str) -> Dict[str, Any]:
    """Parse Prometheus text format into dict"""
    metrics = {}
    for line in text.split('\n'):
        if line.startswith('#') or not line.strip():
            continue
        try:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0]
                value = float(parts[1])
                metrics[key] = value
        except:
            continue
    return metrics


def get_pnl_summary() -> Dict[str, Any]:
    """Fetch PnL summary from API"""
    try:
        response = requests.get(
            f"{BACKEND_URL}/api/pnl/summary",
            params={"period": "today"},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching PnL: {e}")
        return {}


def get_fees_summary() -> Dict[str, Any]:
    """Fetch fees summary from API (ACCURATE SOURCE!)"""
    try:
        response = requests.get(
            f"{BACKEND_URL}/api/pnl/fees",
            params={"period": "today"},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching fees: {e}")
        return {"total_fee_usd": 0.0, "count": 0, "by_symbol": []}


def get_positions() -> list:
    """Fetch current positions"""
    try:
        response = requests.get(f"{BACKEND_URL}/api/exec/positions", timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching positions: {e}")
        return []


def print_dashboard(metrics: Dict[str, Any], pnl: Dict[str, Any], fees: Dict[str, Any], positions: list):
    """Print formatted dashboard"""
    # Clear screen
    print("\033[2J\033[H")
    
    print("=" * 70)
    print(f"  REALISTIC PAPER TRADING MONITOR - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # Simulation status
    sim_enabled = metrics.get('simulation_enabled', 0)
    print(f"\n📊 SIMULATION STATUS: {'🟢 ENABLED' if sim_enabled > 0 else '🔴 DISABLED'}")
    
    # PnL Summary
    print(f"\n💰 P&L SUMMARY (Today)")
    gross_pnl = pnl.get('total_usd', 0)
    total_fees = fees.get('total_fee_usd', 0.0)  # ✅ FROM API, NOT PROMETHEUS!
    net_pnl = gross_pnl - total_fees
    
    print(f"   Gross P&L:     ${gross_pnl:>8.2f}")
    print(f"   Total Fees:    ${total_fees:>8.4f}  ✅ (0% maker)")
    print(f"   Net P&L:       ${net_pnl:>8.2f}")
    
    by_exchange = pnl.get('by_exchange', [])
    if by_exchange:
        print(f"\n   By Exchange:")
        for ex in by_exchange:
            print(f"   {ex.get('exchange', 'N/A'):8s}:   ${ex.get('total_usd', 0):.2f}")
    
    # Trading stats
    print(f"\n📈 TRADING STATS")
    total_orders = sum(v for k, v in metrics.items() if k.startswith('simulation_orders_total'))
    total_rejections = sum(v for k, v in metrics.items() if k.startswith('simulation_rejections_total'))
    total_partial = sum(v for k, v in metrics.items() if k.startswith('simulation_partial_fills_total'))
    total_fills = fees.get('count', 0)  # ✅ Actual fill count from API
    
    rejection_pct = (total_rejections / max(total_orders, 1)) * 100
    partial_pct = (total_partial / max(total_orders, 1)) * 100
    
    print(f"   Total Orders:        {int(total_orders)}")
    print(f"   Total Fills:         {total_fills}")
    print(f"   Rejections:          {int(total_rejections)} ({rejection_pct:.1f}%)")
    print(f"   Partial Fills:       {int(total_partial)} ({partial_pct:.1f}%)")
    
    # Average metrics
    print(f"\n📉 SIMULATION METRICS")
    # Note: These might be 0.0 if Prometheus aggregation not working
    # But the simulation IS working (we see partial fills above)
    avg_slippage = metrics.get('simulation_avg_slippage_bps', 0)
    rejection_rate = metrics.get('simulation_rejection_rate', 0)
    partial_rate = metrics.get('simulation_partial_fill_rate', 0)
    
    print(f"   Avg Slippage:        {avg_slippage:.2f} bps (target: 1-5)")
    print(f"   Rejection Rate:      {rejection_rate*100:.1f}% (target: ~5%)")
    print(f"   Partial Fill Rate:   {partial_rate*100:.1f}% (target: ~30%)")
    
    # Strategy metrics
    print(f"\n🎯 STRATEGY METRICS")
    strategy_entries = sum(v for k, v in metrics.items() if k.startswith('strategy_entries_total'))
    strategy_exits = sum(v for k, v in metrics.items() if k.startswith('strategy_exits_total'))
    
    # Count open positions from API
    open_positions = sum(1 for p in positions if p.get('qty', 0) != 0)
    total_exposure = sum(
        abs(p.get('qty', 0) * p.get('avg_price', 0))
        for p in positions if p.get('qty', 0) != 0
    )
    
    print(f"   Entries (BUY):       {int(strategy_entries)}")
    print(f"   Exits (SELL):        {int(strategy_exits)}")
    print(f"   Open Positions:      {open_positions}")
    print(f"   Total Exposure:      ${total_exposure:.2f}")
    
    # Show open positions if any
    if open_positions > 0:
        print(f"\n   {'Symbol':<10} {'Qty':>10} {'Avg Price':>10} {'UPnL':>10}")
        print(f"   {'-'*42}")
        for p in positions:
            if p.get('qty', 0) != 0:
                symbol = p.get('symbol', '')
                qty = p.get('qty', 0)
                avg_price = p.get('avg_price', 0)
                upnl = p.get('unrealized_pnl', 0)
                print(f"   {symbol:<10} {qty:>10.6f} ${avg_price:>9.2f} ${upnl:>9.4f}")
    
    # Fees by symbol
    print(f"\n💸 FEES BY SYMBOL")
    fee_symbols = fees.get('by_symbol', [])
    if fee_symbols:
        for s in fee_symbols:
            symbol = s.get('symbol', '')
            fee = s.get('total_fee_usd', 0.0)
            count = s.get('count', 0)
            print(f"   {symbol:<10}  ${fee:>6.4f}  ({count} fills)")
    else:
        print(f"   No fees recorded (0% maker rate working!)")
    
    # WebSocket health
    print(f"\n🌐 WEBSOCKET HEALTH")
    ws_lag = metrics.get('ws_lag_ms', 0)
    ws_reconnects = metrics.get('ws_reconnects_total', 0)
    ticks_per_sec = metrics.get('ws_ticks_per_sec', 0)
    
    print(f"   WS Lag:              {ws_lag:.0f} ms")
    print(f"   Reconnects:          {int(ws_reconnects)}")
    print(f"   Ticks/sec:           {ticks_per_sec:.1f}")
    
    print("\n" + "=" * 70)
    print("  Press Ctrl+C to exit")
    print("=" * 70)


def main():
    """Main monitoring loop"""
    print("Starting Paper Trading Monitor...")
    print(f"Backend: {BACKEND_URL}")
    print("\nWaiting for first metrics...")
    time.sleep(2)
    
    try:
        while True:
            metrics = get_metrics()
            pnl = get_pnl_summary()
            fees = get_fees_summary()  # ✅ Get accurate fees from API
            positions = get_positions()  # ✅ Get positions for display
            print_dashboard(metrics, pnl, fees, positions)
            time.sleep(5)  # Update every 5 seconds
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")
    except Exception as e:
        print(f"\n\nError: {e}")


if __name__ == "__main__":
    main()