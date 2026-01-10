"""
FINAL ML EXPORT FOR COLAB
=========================

Export all 53,222 trades with:
- 3 market features (spread, imbalance, depth)
- 5 symbol one-hot features
- 3 time features (hour, day, minute)
- Derived features (ratios, squares)
= TOTAL: ~15 features

Labels:
- Positive (1): TP + TRAIL
- Negative (0): TIMEOUT + SL
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

print("=" * 70)
print("ML EXPORT - ALL TRADES WITH ENGINEERED FEATURES")
print("=" * 70)

# Connect
DB_PATH = 'mexc.db'
conn = sqlite3.connect(DB_PATH)

print(f"\n📂 Database: {DB_PATH}")

# ============================================================
# STEP 1: Load ALL trades
# ============================================================

print("\n[1/4] Loading ALL trades...")

query = """
SELECT 
    symbol,
    entry_time,
    exit_reason,
    
    -- Market features (3)
    spread_bps_entry,
    imbalance_entry,
    depth_5bps_entry,
    
    -- Metadata
    pnl_usd,
    pnl_bps,
    hold_duration_sec,
    
    -- Label: TP/TRAIL = 1, TIMEOUT/SL = 0
    CASE 
        WHEN UPPER(exit_reason) IN ('TP', 'TRAIL', 'TRAILING') THEN 1
        WHEN UPPER(exit_reason) IN ('TIMEOUT', 'TO', 'SL', 'STOP_LOSS', 'STOPLOSS') THEN 0
        WHEN pnl_usd > 0 THEN 1
        ELSE 0
    END as label
    
FROM trades
WHERE exit_reason IS NOT NULL
  AND spread_bps_entry IS NOT NULL
  AND imbalance_entry IS NOT NULL
  AND depth_5bps_entry IS NOT NULL
  AND status = 'CLOSED'
ORDER BY entry_time
"""

df = pd.read_sql_query(query, conn)
conn.close()

print(f"✅ Loaded {len(df):,} trades")

# ============================================================
# STEP 2: Feature Engineering
# ============================================================

print("\n[2/4] Engineering features...")

# Parse datetime
df['entry_datetime'] = pd.to_datetime(df['entry_time'])

# Time features
df['hour_of_day'] = df['entry_datetime'].dt.hour
df['day_of_week'] = df['entry_datetime'].dt.dayofweek  # 0=Monday
df['minute_of_hour'] = df['entry_datetime'].dt.minute

# Fill NaN with safe defaults
df['spread_bps_entry'] = df['spread_bps_entry'].fillna(10.0)
df['imbalance_entry'] = df['imbalance_entry'].fillna(0.5)
df['depth_5bps_entry'] = df['depth_5bps_entry'].fillna(0.0)

# Derived features
df['imbalance_deviation'] = np.abs(df['imbalance_entry'] - 0.5)
df['spread_squared'] = df['spread_bps_entry'] ** 2
df['log_depth'] = np.log1p(df['depth_5bps_entry'])  # log(1 + depth)

# Symbol one-hot encoding
symbols = df['symbol'].unique()
print(f"   Symbols: {', '.join(symbols)}")

for symbol in symbols:
    df[f'symbol_{symbol}'] = (df['symbol'] == symbol).astype(int)

print(f"✅ Created derived features")

# ============================================================
# STEP 3: Select final features
# ============================================================

print("\n[3/4] Preparing final dataset...")

# Base features
base_features = [
    'spread_bps_entry',
    'imbalance_entry',
    'depth_5bps_entry',
]

# Time features
time_features = [
    'hour_of_day',
    'day_of_week',
    'minute_of_hour',
]

# Derived features
derived_features = [
    'imbalance_deviation',
    'spread_squared',
    'log_depth',
]

# Symbol one-hot
symbol_features = [col for col in df.columns if col.startswith('symbol_')]

# All features
all_features = base_features + time_features + derived_features + symbol_features

print(f"   Base features:    {len(base_features)}")
print(f"   Time features:    {len(time_features)}")
print(f"   Derived features: {len(derived_features)}")
print(f"   Symbol features:  {len(symbol_features)}")
print(f"   TOTAL FEATURES:   {len(all_features)}")

# Create final dataset
columns_to_export = ['label'] + all_features + ['symbol', 'entry_time', 'exit_reason', 'pnl_usd']
df_final = df[columns_to_export].copy()

# Handle any remaining NaN/inf
df_final = df_final.replace([np.inf, -np.inf], 0)
df_final = df_final.fillna(0)

print(f"✅ Final dataset: {len(df_final):,} rows × {len(df_final.columns)} columns")

# ============================================================
# STEP 4: Export to CSV
# ============================================================

print("\n[4/4] Exporting to CSV...")

output_dir = Path('ml_data')
output_dir.mkdir(exist_ok=True)

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
output_file = output_dir / f'training_colab_final_{timestamp}.csv'

df_final.to_csv(output_file, index=False)

print(f"✅ Exported to: {output_file}")

# ============================================================
# STATISTICS
# ============================================================

print("\n" + "=" * 70)
print("EXPORT STATISTICS")
print("=" * 70)

print(f"\n📊 Dataset:")
print(f"   Total samples:    {len(df_final):,}")
print(f"   Total features:   {len(all_features)}")

print(f"\n📊 Label Distribution:")
tp_trail = (df_final['label'] == 1).sum()
to_sl = (df_final['label'] == 0).sum()
print(f"   WIN (TP+TRAIL):   {tp_trail:>6,} ({tp_trail/len(df_final)*100:>5.1f}%)")
print(f"   LOSS (TO+SL):     {to_sl:>6,} ({to_sl/len(df_final)*100:>5.1f}%)")
print(f"   Ratio:            {tp_trail/to_sl:.2f}:1")

print(f"\n📊 Exit Reason Breakdown:")
for reason in df_final['exit_reason'].unique():
    count = (df_final['exit_reason'] == reason).sum()
    pct = count / len(df_final) * 100
    avg_pnl = df_final[df_final['exit_reason'] == reason]['pnl_usd'].mean()
    print(f"   {reason:<10} {count:>6,} ({pct:>5.1f}%)  Avg: ${avg_pnl:>7.4f}")

print(f"\n📊 By Symbol:")
for symbol in symbols:
    subset = df_final[df_final['symbol'] == symbol]
    win_rate = (subset['label'] == 1).sum() / len(subset) * 100
    total_pnl = subset['pnl_usd'].sum()
    print(f"   {symbol:<12} {len(subset):>6,} trades, {win_rate:>5.1f}% WR, ${total_pnl:>7.2f}")

print(f"\n📊 Feature List:")
for i, feat in enumerate(all_features, 1):
    print(f"   {i:2d}. {feat}")

print("\n" + "=" * 70)
print("✅ READY FOR COLAB TRAINING!")
print("=" * 70)

print(f"\n📋 NEXT STEPS:")
print(f"   1. Upload {output_file.name} to Google Colab")
print(f"   2. Train XGBoost model")
print(f"   3. Expected accuracy: 73-77%")
print(f"   4. If good → Deploy ML v1")
print(f"   5. If bad → Wait for Phase 2 (full features)")

print(f"\n💡 BASELINE:")
print(f"   Current WR: 69.7%")
print(f"   Target WR:  75%+")
print(f"   Improvement: +5-8%")