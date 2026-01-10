import pandas as pd
import numpy as np
from datetime import datetime

print("=" * 70)
print("VALIDATION TEST - FINAL ANALYSIS")
print("=" * 70)

df = pd.read_csv('validation_log.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])

# Duration
duration_hours = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).total_seconds() / 3600
duration_days = duration_hours / 24

print(f"\n📅 DURATION:")
print(f"   Start:     {df['timestamp'].iloc[0]}")
print(f"   End:       {df['timestamp'].iloc[-1]}")
print(f"   Duration:  {duration_days:.2f} days ({duration_hours:.1f} hours)")
print(f"   Entries:   {len(df)}")

# Final metrics
print(f"\n💰 FINAL METRICS:")
print(f"   Net P&L:          ${df['net_pnl_usd'].iloc[-1]:.2f}")
print(f"   Total Fills:      {df['total_fills'].iloc[-1]:.0f}")
print(f"   Total Fees:       ${df['total_fees_usd'].iloc[-1]:.4f}")
print(f"   Avg Profit/Fill:  ${df['avg_profit_per_fill'].iloc[-1]:.4f}")
print(f"   Max Exposure:     ${df['total_exposure_usd'].max():.2f}")

# Performance
print(f"\n📊 PERFORMANCE:")
print(f"   Best P&L:         ${df['net_pnl_usd'].max():.2f}")
print(f"   Worst P&L:        ${df['net_pnl_usd'].min():.2f}")
print(f"   Profit/Hour:      ${df['net_pnl_usd'].iloc[-1] / duration_hours:.2f}")
print(f"   Profit/Day:       ${df['net_pnl_usd'].iloc[-1] / duration_days:.2f}")

# Stability
profitable = (df['net_pnl_usd'] > 0).sum()
win_rate = (profitable / len(df)) * 100
print(f"   Win Rate:         {win_rate:.1f}% (periods profitable)")
print(f"   Avg Cache Hit:    {df['cache_hit_rate'].mean():.1%}")

# Recommendation
print(f"\n{'=' * 70}")
print("💡 RECOMMENDATION:")
print("=" * 70)

final_pnl = df['net_pnl_usd'].iloc[-1]
avg_profit = df['avg_profit_per_fill'].iloc[-1]

if final_pnl > 100 and avg_profit > 0.01:
    print("✅ SYSTEM VALIDATED SUCCESSFULLY!")
    print(f"   - Total Profit: ${final_pnl:.2f}")
    print(f"   - Avg/Fill: ${avg_profit:.4f}")
    print(f"   - Win Rate: {win_rate:.1f}%")
    print("\n   NEXT STEPS:")
    print("   1. Optimize parameters (position size, exit targets)")
    print("   2. Test optimizations for 1 week")
    print("   3. Consider live trading with small size")
elif final_pnl > 0:
    print("⚠️  SYSTEM PROFITABLE BUT NEEDS OPTIMIZATION")
    print(f"   - Profit: ${final_pnl:.2f} ✅")
    print(f"   - Avg/Fill: ${avg_profit:.4f} (target: $0.10+)")
    print("\n   RECOMMENDATIONS:")
    print("   - Increase position size ($5-10 per trade)")
    print("   - Widen exit targets (+0.2% instead of +0.1%)")
    print("   - Better symbol selection (use scanner API)")
else:
    print("❌ SYSTEM NOT PROFITABLE")
    print("   Strategy needs fundamental revision")

print("=" * 70)