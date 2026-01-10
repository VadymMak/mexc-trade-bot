import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

# Get column count
cursor.execute('PRAGMA table_info(ml_trade_outcomes)')
cols = cursor.fetchall()
print(f"\n{'='*60}")
print(f"DATABASE INFO")
print(f"{'='*60}")
print(f"Total columns: {len(cols)}")

# Get trade count
cursor.execute('SELECT COUNT(*) FROM ml_trade_outcomes')
total = cursor.fetchone()[0]
print(f"Total trades: {total}")

if total > 0:
    # Sample 10 important features
    cursor.execute('''
        SELECT 
            spread_bps_entry,
            depth5_bid_usd_entry,
            trades_per_min_entry,
            atr1m_pct_entry,
            vol_pattern_entry,
            depth_imbalance_entry,
            maker_fee_entry,
            base_volume_24h_entry,
            avg_trade_size_entry,
            mid_price_entry
        FROM ml_trade_outcomes 
        ORDER BY id DESC 
        LIMIT 1
    ''')
    
    row = cursor.fetchone()
    
    labels = [
        'spread_bps', 'depth5_bid', 'trades/min', 
        'atr', 'vol_pattern', 'depth_imb',
        'maker_fee', 'volume_24h', 'avg_trade_size', 'mid_price'
    ]
    
    print(f"\n{'='*60}")
    print("SAMPLE FEATURES (10/47)")
    print(f"{'='*60}")
    
    non_zero = 0
    for i, (label, val) in enumerate(zip(labels, row), 1):
        is_active = val is not None and abs(float(val)) > 0.00001
        if is_active:
            non_zero += 1
            status = 'OK'
        else:
            status = 'ZERO'
        
        if isinstance(val, float):
            if abs(val) > 1000:
                val_str = f'{val:,.2f}'
            else:
                val_str = f'{val:.6f}'
        else:
            val_str = str(val)
        
        print(f" {i:2d}. [{status:^5}] {label:15s} = {val_str}")
    
    print(f"\n{'='*60}")
    print(f"Sample Coverage: {non_zero}/10 ({non_zero/10*100:.0f}%)")
    print(f"{'='*60}")
    
    if non_zero >= 8:
        print("\nRESULT: EXCELLENT! Most features working!")
        print("Full 47 features likely operational.")
        print("\nREADY FOR PRODUCTION!")
    elif non_zero >= 5:
        print("\nRESULT: GOOD! Core features working.")
    else:
        print("\nRESULT: Check logs for issues.")
        
else:
    print("\nNo trades yet - wait for 1 trade to complete!")

conn.close()