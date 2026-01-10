import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

# Get total trades
cursor.execute('SELECT COUNT(*) FROM ml_trade_outcomes')
total = cursor.fetchone()[0]

# Check feature completeness
cursor.execute('''
    SELECT COUNT(*) FROM ml_trade_outcomes 
    WHERE 
        trades_per_min_entry > 0 AND 
        atr1m_pct_entry > 0 AND
        base_volume_24h_entry > 0 AND
        depth5_bid_usd_entry > 0
''')
complete = cursor.fetchone()[0]

# Get sample from latest trade
cursor.execute('''
    SELECT 
        spread_bps_entry, imbalance_entry,
        depth5_bid_usd_entry, depth10_bid_usd_entry,
        trades_per_min_entry, usd_per_min_entry,
        atr1m_pct_entry, grinder_ratio_entry,
        base_volume_24h_entry, scanner_score_entry
    FROM ml_trade_outcomes 
    WHERE trades_per_min_entry > 0
    ORDER BY id DESC LIMIT 1
''')

row = cursor.fetchone()

print('='*70)
print('FINAL SYSTEM CHECK')
print('='*70)
print(f'Total trades collected: {total}')
print(f'Trades with full features: {complete} ({complete/total*100:.1f}%)')
print(f'Target for ML training: 20,000')
print(f'Progress: {total/20000*100:.1f}%')
print('='*70)

if row:
    labels = [
        'spread_bps', 'imbalance', 'depth5_bid', 'depth10_bid',
        'trades/min', 'usd/min', 'atr', 'grinder',
        'volume_24h', 'scanner_score'
    ]
    
    print('\nLATEST COMPLETE TRADE SAMPLE (10 features):')
    non_zero = 0
    for label, val in zip(labels, row):
        if val and abs(float(val)) > 0.00001:
            non_zero += 1
            status = 'OK'
        else:
            status = 'ZERO'
        
        if isinstance(val, float):
            if abs(val) > 1000:
                val_str = f'{val:,.2f}'
            elif abs(val) > 1:
                val_str = f'{val:.4f}'
            else:
                val_str = f'{val:.6f}'
        else:
            val_str = str(val)
        
        print(f'  [{status:^5}] {label:15s} = {val_str}')
    
    print(f'\n  Coverage: {non_zero}/10 features')

print('='*70)
print('SYSTEM STATUS:')

if complete / total >= 0.75:
    print('✅ EXCELLENT - 75%+ trades have full features')
    print('✅ READY FOR PRODUCTION DATA COLLECTION')
    print('\nNEXT STEPS:')
    print('  1. Run on 5 symbols for 5-7 days')
    print('  2. Collect 20,000 trades')
    print('  3. Train ML model v2')
elif complete / total >= 0.50:
    print('✅ GOOD - 50%+ trades have features')
    print('⚠️  Monitor for improvements')
else:
    print('⚠️  LOW COVERAGE - Check logs for issues')

print('='*70)

conn.close()