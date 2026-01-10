import pandas as pd
import numpy as np
from datetime import datetime

print("=" * 70)
print("VALIDATION TEST RESULTS - FINAL ANALYSIS")
print("=" * 70)

# Load data
df = pd.read_csv('validation_log.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])

# Basic stats
print(f"\nDURATION:")
print(f"  Start:     {df['timestamp'].iloc[0]}")
print(f"  End:       {df['timestamp'].iloc[-1]}")
duration_hours = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).total_seconds() / 3600
print(f"  Duration:  {duration_hours:.1f} hours ({duration_hours/24:.1f} days)")
print(f"  Entries:   {len(df)}")

# Final metrics
print(f"\nFINAL METRICS:")
print(f"  Net P&L:          ${df['net_pnl_usd'].iloc[-1]:.2f}")
print(f"  Total Fills:      {df['total_fills'].iloc[-1]:.0f}")
print(f"  Total Fees:       ${df['total_fees_usd'].iloc[-1]:.4f}")
print(f"  Avg Profit/Fill:  ${df['avg_profit_per_fill'].iloc[-1]:.4f}")
print(f"  Max Exposure:     ${df['total_exposure_usd'].max():.2f}")

# Performance
print(f"\nPERFORMANCE:")
print(f"  Best P&L:         ${df['net_pnl_usd'].max():.2f}")
print(f"  Worst P&L:        ${df['net_pnl_usd'].min():.2f}")
print(f"  P&L Range:        ${df['net_pnl_usd'].max() - df['net_pnl_usd'].min():.2f}")
print(f"  Profit/Hour:      ${df['net_pnl_usd'].iloc[-1] / duration_hours:.2f}")

# Win rate
profitable = (df['net_pnl_usd'] > 0).sum()
win_rate = (profitable / len(df)) * 100
print(f"  Win Rate:         {win_rate:.1f}% (of logged periods)")

# Cache
print(f"  Avg Cache Hit:    {df['cache_hit_rate'].mean():.1%}")

# Decision
print(f"\n{'=' * 70}")
print("RECOMMENDATION:")
print("=" * 70)

final_pnl = df['net_pnl_usd'].iloc[-1]
avg_profit = df['avg_profit_per_fill'].iloc[-1]

if final_pnl > 50 and avg_profit > 0.10:
    print("✅ SYSTEM READY FOR LIVE TRADING")
    print(f"   - Profitable: ${final_pnl:.2f}")
    print(f"   - Meets targets: ${avg_profit:.4f} > $0.10/fill")
    print(f"   - Stable: {win_rate:.1f}% win rate")
elif final_pnl > 0 and avg_profit > 0.05:
    print("⚠️  SYSTEM PROFITABLE - NEEDS OPTIMIZATION")
    print(f"   - Profitable: ${final_pnl:.2f} ✅")
    print(f"   - Below target: ${avg_profit:.4f} < $0.10/fill ⚠️")
    print(f"   - Win rate: {win_rate:.1f}%")
    print(f"\n   OPTIMIZE:")
    print(f"   - Increase position size (currently ~$2)")
    print(f"   - Widen exit target (currently +0.1%)")
    print(f"   - Better symbol selection (use /api/scanner/*/top)")
elif final_pnl > 0:
    print("🔶 SYSTEM MARGINALLY PROFITABLE")
    print(f"   - Profit: ${final_pnl:.2f}")
    print(f"   - Avg: ${avg_profit:.4f}/fill")
    print(f"\n   REQUIRES SIGNIFICANT OPTIMIZATION")
else:
    print("❌ SYSTEM NOT PROFITABLE")
    print(f"   - Loss: ${final_pnl:.2f}")
    print(f"   - Strategy needs fundamental revision")

print("=" * 70)