#!/usr/bin/env python3
"""MM Bot Pattern Analysis - Quick Version"""

import sqlite3
import pandas as pd

conn = sqlite3.connect('mexc.db')

# Load data
df = pd.read_sql("""
    SELECT 
        symbol,
        median_trade_usd_entry,
        pnl_bps,
        timed_out,
        win
    FROM ml_trade_outcomes
    WHERE median_trade_usd_entry IS NOT NULL
      AND median_trade_usd_entry > 0
""", conn)

print("\n" + "=" * 70)
print("MM BOT PATTERN ANALYSIS")
print("=" * 70)
print(f"\nTotal trades: {len(df)}\n")

print("BY SYMBOL:")
print("-" * 70)

for symbol in sorted(df['symbol'].unique()):
    sdf = df[df['symbol'] == symbol]
    
    # Estimate MM size
    median = sdf['median_trade_usd_entry'].median()
    mm_size = max(median * 8, 500)
    
    # Stats
    total = len(sdf)
    wins = sdf['win'].sum()
    timeouts = sdf['timed_out'].sum()
    
    win_rate = (wins / total * 100)
    timeout_rate = (timeouts / total * 100)
    
    # Position ratio ($50 position)
    ratio = 50 / mm_size
    
    status = "✅" if ratio < 0.15 else "⚠️" if ratio < 0.25 else "❌"
    
    print(f"\n{symbol:<12} {status}")
    print(f"  Median trade: ${median:6.0f}")
    print(f"  Est MM size:  ${mm_size:6.0f}")
    print(f"  Pos ratio:    {ratio:5.1%}  ($50 / ${mm_size:.0f})")
    print(f"  Trades:       {total:4d}")
    print(f"  Win rate:     {win_rate:5.1f}%")
    print(f"  Timeout rate: {timeout_rate:5.1f}%")

# Correlation analysis
print("\n" + "=" * 70)
print("CORRELATION ANALYSIS")
print("=" * 70 + "\n")

results = []
for symbol in df['symbol'].unique():
    sdf = df[df['symbol'] == symbol]
    median = sdf['median_trade_usd_entry'].median()
    mm_size = max(median * 8, 500)
    ratio = 50 / mm_size
    
    total = len(sdf)
    win_rate = (sdf['win'].sum() / total * 100)
    timeout_rate = (sdf['timed_out'].sum() / total * 100)
    
    results.append({
        'symbol': symbol,
        'position_ratio': ratio,
        'win_rate': win_rate,
        'timeout_rate': timeout_rate
    })

results_df = pd.DataFrame(results)

print("Data for correlation:")
print(results_df.to_string(index=False))
print()

corr_timeout = results_df['position_ratio'].corr(results_df['timeout_rate'])
corr_win = results_df['position_ratio'].corr(results_df['win_rate'])

print(f"📊 Position ratio vs Timeout rate: {corr_timeout:+.3f}")
print(f"📊 Position ratio vs Win rate:     {corr_win:+.3f}")
print()

# Recommendation
if abs(corr_timeout) > 0.6 or abs(corr_win) > 0.5:
    print("🔴 STRONG correlation detected!")
    print("   MM-aware sizing is HIGHLY RECOMMENDED for dataset #2")
    print("   Expected improvement: +2-4% win rate, -3-5% timeout rate")
elif abs(corr_timeout) > 0.3 or abs(corr_win) > 0.3:
    print("🟡 MODERATE correlation detected")
    print("   MM-aware sizing may help for dataset #2")
    print("   Consider A/B testing (50% static, 50% mm-aware)")
else:
    print("🟢 WEAK correlation")
    print("   MM-aware sizing not critical")
    print("   Focus on other optimizations (ML model, risk management)")

print("\n" + "=" * 70 + "\n")

conn.close()