import sqlite3
import json
from datetime import datetime
import math

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

print("\n" + "="*60)
print("ML DATA EXPORT - FULL FEATURES")
print("="*60)

# Load trades
print("\n[1/3] Loading trades...")
cursor.execute('''
    SELECT 
        symbol, entry_time, exit_reason,
        spread_bps_entry, imbalance_entry, depth_5bps_entry,
        pnl_usd, pnl_bps, hold_duration_sec
    FROM trades
    WHERE exit_reason IS NOT NULL
    ORDER BY entry_time
''')

trades = cursor.fetchall()
print(f"   Loaded {len(trades):,} trades")

# Match with snapshots
print("\n[2/3] Matching with snapshots...")
training_data = []
matched = 0
unmatched = 0

for i, trade in enumerate(trades):
    if i % 1000 == 0 and i > 0:
        print(f"   Processing... {i:,}/{len(trades):,}")
    
    symbol, entry_time, exit_reason = trade[0], trade[1], trade[2]
    
    cursor.execute('''
        SELECT 
            atr1m_pct, grinder_ratio, pullback_median_retrace,
            trades_per_min, usd_per_min, median_trade_usd,
            depth5_bid_usd, depth5_ask_usd,
            depth10_bid_usd, depth10_ask_usd
        FROM ml_snapshots
        WHERE symbol = ?
        AND datetime(created_at) BETWEEN 
            datetime(?, '-5 seconds') AND datetime(?, '+5 seconds')
        ORDER BY ABS(
            CAST(strftime('%s', created_at) AS INTEGER) - 
            CAST(strftime('%s', ?) AS INTEGER)
        )
        LIMIT 1
    ''', (symbol, entry_time, entry_time, entry_time))
    
    snapshot = cursor.fetchone()
    
    if snapshot:
        matched += 1
        
        spread_bps = trade[3]
        imbalance = trade[4]
        depth_5bps = trade[5]
        atr1m_pct = snapshot[0] or 0.0
        grinder_ratio = snapshot[1] or 0.0
        pullback = snapshot[2] or 0.0
        tpm = snapshot[3] or 0.0
        usdpm = snapshot[4] or 0.0
        median_trade = snapshot[5] or 0.0
        depth5_bid = snapshot[6] or 0.0
        depth5_ask = snapshot[7] or 0.0
        depth10_bid = snapshot[8] or 0.0
        depth10_ask = snapshot[9] or 0.0
        
        features = {
            'spread_bps_entry': spread_bps,
            'imbalance_entry': imbalance,
            'depth_5bps_entry': depth_5bps,
            'atr1m_pct': atr1m_pct,
            'grinder_ratio': grinder_ratio,
            'pullback_median_retrace': pullback,
            'trades_per_min': tpm,
            'usd_per_min': usdpm,
            'median_trade_usd': median_trade,
            'depth5_bid_usd': depth5_bid,
            'depth5_ask_usd': depth5_ask,
            'depth10_bid_usd': depth10_bid,
            'depth10_ask_usd': depth10_ask,
            'imbalance_deviation': abs(imbalance - 0.5),
            'depth_spread_ratio': depth_5bps / (spread_bps + 0.001),
            'atr_spread_ratio': atr1m_pct / (spread_bps + 0.001) if atr1m_pct > 0 else 0,
            'depth_imbalance_interaction': depth_5bps * abs(imbalance - 0.5),
            'log_depth': math.log(depth_5bps + 1) if depth_5bps > 0 else 0,
            'spread_squared': spread_bps ** 2,
            'volume_stability': usdpm / (tpm * median_trade + 0.001) if tpm > 0 and median_trade > 0 else 0,
            'depth_ratio_10_5': (depth10_bid + depth10_ask) / (depth5_bid + depth5_ask + 0.001) if depth5_bid + depth5_ask > 0 else 1.0,
        }
        
        label = 1 if exit_reason == 'TP' else 0
        
        training_data.append({
            'symbol': symbol,
            'entry_time': entry_time,
            'features': features,
            'label': label,
            'exit_reason': exit_reason,
            'pnl_usd': trade[6],
            'pnl_bps': trade[7]
        })
    else:
        unmatched += 1

print(f"\n   Matched: {matched:,}")
print(f"   Unmatched: {unmatched:,}")

# Save
print("\n[3/3] Saving to file...")
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f'ml_data/training_full_features_{timestamp}.json'

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(training_data, f, indent=2)

tp_count = sum(1 for d in training_data if d['label'] == 1)
sl_count = len(training_data) - tp_count

print("\n" + "="*60)
print("EXPORT COMPLETE!")
print("="*60)
print(f"File:     {output_file}")
print(f"Samples:  {len(training_data):,}")
print(f"Features: {len(list(training_data[0]['features'].keys()))}")
print(f"TP:       {tp_count:,} ({tp_count/len(training_data)*100:.1f}%)")
print(f"SL/TO:    {sl_count:,} ({sl_count/len(training_data)*100:.1f}%)")
print("="*60)

conn.close()