"""
Export ML training data with proper labels (IMPROVED VERSION)
Positive examples: TP exits
Negative examples: TIMEOUT + SL exits
"""
import sqlite3
import pandas as pd
from pathlib import Path
import sys
from datetime import datetime

print("=" * 70)
print("ML DATA EXPORT WITH LABELS (V2 - WITH SL)")
print("=" * 70)

# Find database
db_path = 'mexc.db'
if not Path(db_path).exists():
    print("❌ mexc.db not found!")
    sys.exit(1)

# Connect
conn = sqlite3.connect(db_path)

# Export trades with features
print("\n📊 Exporting trades with features...")

query = """
SELECT 
    t.symbol,
    t.entry_time,
    t.exit_time,
    t.exit_reason,
    t.pnl_usd,
    t.pnl_bps,
    t.pnl_percent,
    t.hold_duration_sec,
    t.spread_bps_entry,
    t.imbalance_entry,
    t.depth_5bps_entry,
    t.entry_fee,
    t.exit_fee,
    t.total_fee,
    
    -- Additional features from strategy_params (JSON)
    t.strategy_params,
    
    -- Label (target variable) - IMPROVED WITH SL
    CASE 
        -- SUCCESS: Take Profit
        WHEN UPPER(t.exit_reason) = 'TP' THEN 1
        
        -- FAIL: Timeout (slow market, no profit)
        WHEN UPPER(t.exit_reason) IN ('TIMEOUT', 'TO') THEN 0
        
        -- FAIL: Stop Loss (market moved against, loss)
        WHEN UPPER(t.exit_reason) IN ('SL', 'STOP_LOSS', 'STOPLOSS') THEN 0
        
        -- Fallback: Use P&L if exit_reason unclear
        WHEN t.pnl_usd > 0 THEN 1
        WHEN t.pnl_usd <= 0 THEN 0
        
        ELSE NULL
    END as label,
    
    -- Exit type for analysis
    CASE 
        WHEN UPPER(t.exit_reason) = 'TP' THEN 'TP'
        WHEN UPPER(t.exit_reason) IN ('TIMEOUT', 'TO') THEN 'TIMEOUT'
        WHEN UPPER(t.exit_reason) IN ('SL', 'STOP_LOSS', 'STOPLOSS') THEN 'SL'
        ELSE 'OTHER'
    END as exit_type
    
FROM trades t
WHERE t.created_at > datetime('now', '-48 hours')
  AND t.exit_reason IS NOT NULL
ORDER BY t.entry_time
"""

df = pd.read_sql_query(query, conn)
print(f"✅ Loaded {len(df)} trades")

# Filter only labeled data
df_labeled = df[df['label'].notna()].copy()
print(f"✅ Labeled trades: {len(df_labeled)}")

# Count distribution BY EXIT TYPE
print(f"\n📊 EXIT REASON BREAKDOWN:")
exit_counts = df_labeled['exit_type'].value_counts()
for exit_type, count in exit_counts.items():
    pct = count / len(df_labeled) * 100
    avg_pnl = df_labeled[df_labeled['exit_type'] == exit_type]['pnl_usd'].mean()
    
    if exit_type == 'TP':
        marker = "✅"
    elif exit_type == 'TIMEOUT':
        marker = "⚠️"
    elif exit_type == 'SL':
        marker = "❌"
    else:
        marker = "❓"
    
    print(f"  {marker} {exit_type:<10} {count:>6} ({pct:>5.1f}%)  Avg P&L: ${avg_pnl:>7.4f}")

# Label distribution
positive = (df_labeled['label'] == 1).sum()
negative = (df_labeled['label'] == 0).sum()

print(f"\n📊 LABEL DISTRIBUTION:")
print(f"  ✅ Positive (label=1): {positive:>6} ({positive/len(df_labeled)*100:.1f}%)")
print(f"  ❌ Negative (label=0): {negative:>6} ({negative/len(df_labeled)*100:.1f}%)")

# Breakdown of negatives
timeout_count = (df_labeled['exit_type'] == 'TIMEOUT').sum()
sl_count = (df_labeled['exit_type'] == 'SL').sum()

if negative > 0:
    print(f"\n     Negative breakdown:")
    print(f"       TIMEOUT: {timeout_count:>6} ({timeout_count/negative*100:.1f}% of negatives)")
    print(f"       SL:      {sl_count:>6} ({sl_count/negative*100:.1f}% of negatives)")

# Check balance
print(f"\n📊 BALANCE ASSESSMENT:")
if negative < positive * 0.1:
    print("⚠️  WARNING: Very imbalanced dataset!")
    print("   Too few negative examples (<10% of positives)")
    print("   Consider collecting more timeout/SL examples")
elif negative > positive * 0.5:
    print("⚠️  WARNING: Too many negatives!")
    print("   >50% negative examples")
    print("   Model may learn to avoid trading entirely")
    print("   Consider:")
    print("   - Tightening entry filters")
    print("   - Optimizing TP/SL parameters")
else:
    print("✅ Good balance for training!")
    print(f"   Ratio positive:negative = {positive/negative:.2f}:1")

# Symbol distribution
print(f"\n📊 STATISTICS BY SYMBOL:")
symbol_stats = df_labeled.groupby('symbol').agg({
    'label': ['count', 'sum', 'mean'],
    'pnl_usd': 'mean',
    'hold_duration_sec': 'mean'
}).round(3)
symbol_stats.columns = ['total_trades', 'tp_count', 'tp_rate', 'avg_pnl', 'avg_hold_sec']
symbol_stats = symbol_stats.sort_values('tp_rate', ascending=False)

print(f"\n{'SYMBOL':<12} {'TOTAL':<7} {'TPs':<6} {'TP_RATE':<9} {'AVG_PNL':<10} {'AVG_HOLD':<10}")
print("-" * 70)
for symbol, row in symbol_stats.iterrows():
    tp_rate_pct = row['tp_rate'] * 100
    
    if tp_rate_pct >= 90:
        marker = "🟢"
    elif tp_rate_pct >= 70:
        marker = "🟡"
    else:
        marker = "🔴"
    
    print(f"{marker} {symbol:<10} {row['total_trades']:<7.0f} {row['tp_count']:<6.0f} "
          f"{tp_rate_pct:<8.1f}% ${row['avg_pnl']:<9.4f} {row['avg_hold_sec']:<9.1f}s")

# Identify problematic symbols
print(f"\n📊 PROBLEM SYMBOLS (TP rate < 50%):")
problem_symbols = symbol_stats[symbol_stats['tp_rate'] < 0.5]
if len(problem_symbols) > 0:
    for symbol, row in problem_symbols.iterrows():
        print(f"  ❌ {symbol}: {row['tp_rate']*100:.1f}% TP rate "
              f"({row['tp_count']:.0f}/{row['total_trades']:.0f})")
    print(f"\n  💡 Recommendation: Consider blacklisting these symbols")
    print(f"     OR let ML learn to filter them automatically")
else:
    print(f"  ✅ No problem symbols found!")

# Export to CSV
output_dir = Path('ml_data')
output_dir.mkdir(exist_ok=True)

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
output_file = output_dir / f'training_data_v5_{timestamp}.csv'
df_labeled.to_csv(output_file, index=False)

print(f"\n✅ Exported to: {output_file}")
print(f"   Total records: {len(df_labeled)}")

# Create symlink to latest
latest_link = output_dir / 'training_data_latest.csv'
if latest_link.exists():
    latest_link.unlink()
try:
    latest_link.symlink_to(output_file.name)
    print(f"✅ Symlink created: {latest_link}")
except:
    # Windows fallback: just copy
    import shutil
    shutil.copy(output_file, latest_link)
    print(f"✅ Copy created: {latest_link}")

# Export samples by exit type
print(f"\n📁 Creating sample files by exit type...")

tp_samples = df_labeled[df_labeled['exit_type'] == 'TP'].head(100)
timeout_samples = df_labeled[df_labeled['exit_type'] == 'TIMEOUT'].head(100)
sl_samples = df_labeled[df_labeled['exit_type'] == 'SL'].head(100)

tp_samples.to_csv(output_dir / 'sample_TP.csv', index=False)
print(f"  ✅ sample_TP.csv ({len(tp_samples)} examples)")

if len(timeout_samples) > 0:
    timeout_samples.to_csv(output_dir / 'sample_TIMEOUT.csv', index=False)
    print(f"  ⚠️  sample_TIMEOUT.csv ({len(timeout_samples)} examples)")

if len(sl_samples) > 0:
    sl_samples.to_csv(output_dir / 'sample_SL.csv', index=False)
    print(f"  ❌ sample_SL.csv ({len(sl_samples)} examples)")

# Feature statistics comparison
print(f"\n📊 FEATURE COMPARISON (TP vs TIMEOUT vs SL):")

features_to_compare = ['spread_bps_entry', 'imbalance_entry', 'depth_5bps_entry', 'pnl_usd', 'hold_duration_sec']

for feature in features_to_compare:
    print(f"\n{feature.upper()}:")
    
    tp_mean = df_labeled[df_labeled['exit_type'] == 'TP'][feature].mean()
    print(f"  ✅ TP:      {tp_mean:>10.2f}")
    
    if timeout_count > 0:
        timeout_mean = df_labeled[df_labeled['exit_type'] == 'TIMEOUT'][feature].mean()
        print(f"  ⚠️  TIMEOUT: {timeout_mean:>10.2f}")
    
    if sl_count > 0:
        sl_mean = df_labeled[df_labeled['exit_type'] == 'SL'][feature].mean()
        print(f"  ❌ SL:      {sl_mean:>10.2f}")

# Feature correlation with label
print(f"\n📊 FEATURE CORRELATION WITH SUCCESS (label=1):")
correlations = df_labeled[['spread_bps_entry', 'imbalance_entry', 'depth_5bps_entry', 'label']].corr()['label'].sort_values(ascending=False)

for feature, corr in correlations.items():
    if feature != 'label':
        if abs(corr) > 0.1:
            marker = "⚠️" if corr < 0 else "✅"
        else:
            marker = "➖"
        print(f"  {marker} {feature:<25} {corr:>7.3f}")

print(f"\n💡 Interpretation:")
print(f"   Positive correlation: Higher value → More likely TP")
print(f"   Negative correlation: Higher value → More likely TIMEOUT/SL")

# P&L statistics
print(f"\n📊 P&L STATISTICS:")
print(f"\nBy exit type:")
for exit_type in ['TP', 'TIMEOUT', 'SL']:
    subset = df_labeled[df_labeled['exit_type'] == exit_type]
    if len(subset) > 0:
        total_pnl = subset['pnl_usd'].sum()
        avg_pnl = subset['pnl_usd'].mean()
        median_pnl = subset['pnl_usd'].median()
        
        if exit_type == 'TP':
            marker = "✅"
        elif exit_type == 'TIMEOUT':
            marker = "⚠️"
        else:
            marker = "❌"
        
        print(f"  {marker} {exit_type:<10} Total: ${total_pnl:>8.2f}  "
              f"Avg: ${avg_pnl:>7.4f}  Median: ${median_pnl:>7.4f}")

# Summary
print(f"\n{'='*70}")
print("✅ EXPORT COMPLETE!")
print(f"{'='*70}")

print(f"\n📊 SUMMARY:")
print(f"   Total trades:     {len(df_labeled)}")
print(f"   Positive (TP):    {positive} ({positive/len(df_labeled)*100:.1f}%)")
print(f"   Negative (TO+SL): {negative} ({negative/len(df_labeled)*100:.1f}%)")
print(f"   - TIMEOUT:        {timeout_count}")
print(f"   - SL:             {sl_count}")

print(f"\n📁 FILES CREATED:")
print(f"   Main dataset:     {output_file}")
print(f"   Latest link:      ml_data/training_data_latest.csv")
print(f"   TP samples:       ml_data/sample_TP.csv")
if timeout_count > 0:
    print(f"   TIMEOUT samples:  ml_data/sample_TIMEOUT.csv")
if sl_count > 0:
    print(f"   SL samples:       ml_data/sample_SL.csv")

print(f"\n📋 NEXT STEPS:")
print(f"   1. Review sample files to understand patterns")
print(f"   2. Run: python scripts/ml_train_v5.py")
print(f"   3. Evaluate model performance")

if len(problem_symbols) > 0:
    print(f"\n⚠️  RECOMMENDATION:")
    print(f"   Consider blacklisting problem symbols:")
    for symbol in problem_symbols.index:
        print(f"      - {symbol}")

conn.close()

print(f"\n{'='*70}")
print("🚀 READY FOR ML TRAINING!")
print(f"{'='*70}")