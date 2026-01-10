import sqlite3
import json
from datetime import datetime
from collections import Counter

print("Exporting training data from baseline...")

# Load from baseline backup (7,589 trades with labels)
conn = sqlite3.connect('mexc_backup_baseline_2025-10-30.db')
cursor = conn.cursor()

# Get all trades with features
cursor.execute('''
    SELECT 
        symbol,
        entry_time,
        spread_bps_entry,
        imbalance_entry,
        depth_5bps_entry,
        exit_reason,
        pnl_usd,
        pnl_bps,
        hold_duration_sec
    FROM trades
''')

rows = cursor.fetchall()
conn.close()

# Convert to training format
training_data = []
for row in rows:
    symbol, entry_time, spread, imb, depth, exit_reason, pnl_usd, pnl_bps, duration = row
    
    # Create label (1 = TP, 0 = SL/TIMEOUT)
    label = 1 if exit_reason == 'TP' else 0
    
    training_data.append({
        'symbol': symbol,
        'entry_time': entry_time,
        'features': {
            'spread_bps_entry': spread if spread else 0,
            'imbalance_entry': imb if imb else 0.5,
            'depth_5bps_entry': depth if depth else 0
        },
        'label': label,
        'exit_reason': exit_reason,
        'pnl_usd': pnl_usd,
        'pnl_bps': pnl_bps,
        'hold_duration_sec': duration
    })

# Save as JSON for Colab
output_file = 'ml_data/training_baseline_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.json'

with open(output_file, 'w') as f:
    json.dump(training_data, f, indent=2)

print(f"\nExported {len(training_data)} samples")
print(f"File: {output_file}")
print(f"\nDistribution:")

tp_count = sum(1 for d in training_data if d['label'] == 1)
sl_count = len(training_data) - tp_count

print(f"Positive (TP):     {tp_count:,} ({tp_count/len(training_data)*100:.1f}%)")
print(f"Negative (SL/TO):  {sl_count:,} ({sl_count/len(training_data)*100:.1f}%)")

# Group by symbol
symbol_counts = Counter(d['symbol'] for d in training_data)
print(f"\nBy symbol:")
for symbol, count in symbol_counts.most_common():
    symbol_tp = sum(1 for d in training_data if d['symbol'] == symbol and d['label'] == 1)
    print(f"{symbol:10s} {count:5,} trades ({symbol_tp/count*100:.1f}% TP)")

print(f"\nReady for Colab training!")
print(f"Upload: {output_file}")