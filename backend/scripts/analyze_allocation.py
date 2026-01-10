"""
Analyze Dynamic Allocation Performance
Compare equal vs dynamic allocation results
"""
import sqlite3
import sys
import codecs
import requests
from pathlib import Path
from collections import defaultdict

# Set UTF-8 encoding for Windows
if sys.platform == 'win32':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Find database
db_paths = ['mexc.db', '../mexc.db', 'app.db']
db_path = None
for p in db_paths:
    if Path(p).exists():
        db_path = p
        break

if not db_path:
    print("[ERROR] Database not found!")
    sys.exit(1)

print(f"[OK] Using database: {db_path}\n")

# Get current allocation from API
print("=" * 80)
print("CURRENT ALLOCATION SETTINGS")
print("=" * 80)

try:
    response = requests.get("http://localhost:8000/api/allocation/calculate", timeout=5)
    if response.status_code == 200:
        alloc_data = response.json()
        
        print(f"\nMode: {alloc_data['mode'].upper()}")
        print(f"Total Capital: ${alloc_data['total_capital']:.2f}")
        print(f"Position Size: ${alloc_data['position_size_usd']:.2f}")
        print(f"Active Symbols: {len(alloc_data['active_symbols'])}")
        
        if alloc_data['allocations']:
            print(f"\n{'Symbol':<12} {'Allocated':<12} {'%':<8} {'Max Pos':<8} {'Depth@5bps':<12}")
            print("-" * 65)
            
            for symbol, data in alloc_data['allocations'].items():
                depth_str = f"${data['depth_5bps']:.0f}" if data['depth_5bps'] else "N/A"
                print(
                    f"{symbol:<12} ${data['allocated_usd']:<11.2f} "
                    f"{data['allocation_pct']:<7.1f}% {data['max_positions']:<8} {depth_str:<12}"
                )
    else:
        print(f"[WARN] Could not fetch allocation data: {response.status_code}")
except Exception as e:
    print(f"[WARN] API not available: {e}")

# Connect to database
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Analyze trades by symbol
print("\n" + "=" * 80)
print("TRADE PERFORMANCE BY SYMBOL")
print("=" * 80)

query = '''
    SELECT 
        symbol,
        COUNT(*) as trade_count,
        SUM(CASE WHEN UPPER(exit_reason) = 'TP' THEN 1 ELSE 0 END) as tp_count,
        SUM(CASE WHEN UPPER(exit_reason) IN ('TRAIL', 'TRAILING') THEN 1 ELSE 0 END) as trail_count,
        ROUND(SUM(pnl_usd), 2) as total_pnl,
        ROUND(AVG(pnl_usd), 4) as avg_pnl,
        ROUND(AVG(pnl_bps), 2) as avg_bps,
        ROUND(AVG(hold_duration_sec), 1) as avg_duration,
        COUNT(*) * 100.0 / (SELECT COUNT(*) FROM trades) as pct_of_total
    FROM trades
    WHERE created_at > datetime('now', '-24 hours')
    GROUP BY symbol
    ORDER BY total_pnl DESC
'''

c.execute(query)
results = c.fetchall()

if results:
    print(f"\n{'Symbol':<12} {'Trades':<8} {'TP':<5} {'TRAIL':<6} {'Total $':<10} {'Avg $':<10} {'Avg bps':<8} {'Avg Dur':<8} {'% Total':<8}")
    print("-" * 85)
    
    total_trades = sum(r[1] for r in results)
    total_pnl = sum(r[4] for r in results)
    
    for row in results:
        symbol, trades, tp, trail, t_pnl, a_pnl, a_bps, a_dur, pct = row
        
        # Color coding
        if t_pnl > 0.5:
            marker = "[GREEN]"
        elif t_pnl > 0:
            marker = "[YELLOW]"
        else:
            marker = "[RED]"
        
        print(
            f"{marker} {symbol:<10} {trades:<8} {tp:<5} {trail:<6} "
            f"${t_pnl:<9.2f} ${a_pnl:<9.4f} {a_bps:<7.2f} {a_dur:<7.1f}s {pct:<7.1f}%"
        )
    
    print("-" * 85)
    print(f"{'TOTAL':<12} {total_trades:<8} {'':5} {'':6} ${total_pnl:<9.2f}")

# Calculate allocation efficiency
print("\n" + "=" * 80)
print("ALLOCATION EFFICIENCY ANALYSIS")
print("=" * 80)

if results and 'allocations' in locals() and alloc_data['allocations']:
    print("\nComparing actual performance vs allocated capital:\n")
    print(f"{'Symbol':<12} {'Allocated':<12} {'Actual PnL':<12} {'ROI%':<8} {'Trade %':<8} {'Efficiency':<12}")
    print("-" * 75)
    
    for row in results:
        symbol = row[0]
        actual_pnl = row[4]
        trade_pct = row[8]
        
        if symbol in alloc_data['allocations']:
            allocated = alloc_data['allocations'][symbol]['allocated_usd']
            roi = (actual_pnl / allocated * 100) if allocated > 0 else 0
            
            # Efficiency = (profit share / capital share)
            capital_pct = allocated / alloc_data['total_capital'] * 100
            efficiency = (actual_pnl / total_pnl * 100) / capital_pct if capital_pct > 0 else 0
            
            if efficiency > 1.2:
                eff_marker = "[GREEN] OVERPERFORMING"
            elif efficiency > 0.8:
                eff_marker = "[YELLOW] FAIR"
            else:
                eff_marker = "[RED] UNDERPERFORMING"
            
            print(
                f"{symbol:<12} ${allocated:<11.2f} ${actual_pnl:<11.2f} "
                f"{roi:<7.2f}% {trade_pct:<7.1f}% {eff_marker}"
            )

# Recommendations
print("\n" + "=" * 80)
print("RECOMMENDATIONS")
print("=" * 80)

if alloc_data['mode'] == 'equal':
    print("\n[IDEA] You're using EQUAL allocation")
    print("  - All symbols get same capital regardless of liquidity")
    print("  - Safe and simple approach")
    print("\n[NEXT] Try DYNAMIC allocation:")
    print("  - Symbols with higher liquidity get more capital")
    print("  - May improve overall returns")
    print("  - Switch in Settings page or via API")
elif alloc_data['mode'] == 'dynamic':
    print("\n[OK] You're using DYNAMIC allocation")
    print("  - Capital distributed by liquidity (depth@5bps)")
    print("  - High-liquidity symbols can handle larger positions")
    
    # Find best/worst performers
    if results:
        best = max(results, key=lambda x: x[4])
        worst = min(results, key=lambda x: x[4])
        
        print(f"\n[STAR] Best performer: {best[0]} (${best[4]:.2f})")
        print(f"[WARN] Worst performer: {worst[0]} (${worst[4]:.2f})")
        
        if best[0] in alloc_data['allocations'] and worst[0] in alloc_data['allocations']:
            best_alloc = alloc_data['allocations'][best[0]]['allocated_usd']
            worst_alloc = alloc_data['allocations'][worst[0]]['allocated_usd']
            
            print(f"\n[INFO] Capital allocation:")
            print(f"  {best[0]}: ${best_alloc:.0f} ({alloc_data['allocations'][best[0]]['allocation_pct']:.1f}%)")
            print(f"  {worst[0]}: ${worst_alloc:.0f} ({alloc_data['allocations'][worst[0]]['allocation_pct']:.1f}%)")

conn.close()

print("\n" + "=" * 80)
print("[OK] Allocation analysis complete!")
print("=" * 80)
print("\nTo switch allocation mode:")
print("  UI:  http://localhost:5173/settings")
print("  API: curl -X POST http://localhost:8000/api/allocation/mode -d '{\"mode\":\"dynamic\"}'")