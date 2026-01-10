import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

# Get column count
cursor.execute('PRAGMA table_info(ml_trade_outcomes)')
all_cols = cursor.fetchall()
print(f'Total columns in table: {len(all_cols)}')

# Entry features only
entry_features = [col[1] for col in all_cols if col[1].endswith('_entry')]
print(f'Entry features (_entry suffix): {len(entry_features)}')

# Get latest complete trade
cursor.execute(f"SELECT * FROM ml_trade_outcomes WHERE trades_per_min_entry > 0 ORDER BY id DESC LIMIT 1")
row = cursor.fetchone()

# Count non-null entry features
non_zero = 0
for col_info in all_cols:
    col_name = col_info[1]
    if col_name.endswith('_entry'):
        idx = col_info[0]
        val = row[idx]
        if val is not None and (col_name == 'zero_fee_entry' or abs(float(val)) > 0.00001):
            non_zero += 1

print(f'\nEntry features with data: {non_zero}/{len(entry_features)}')
print(f'Coverage: {non_zero/len(entry_features)*100:.1f}%')

if non_zero >= 45:
    print('\nEXCELLENT! 45+ entry features working!')
    print('READY FOR ML TRAINING!')
elif non_zero >= 35:
    print('\nGOOD! 35+ entry features working!')
elif non_zero >= 25:
    print('\nFAIR - 25+ features')
else:
    print('\nNeed more features')

conn.close()