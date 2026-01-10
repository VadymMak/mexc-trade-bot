import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

# Check if table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ml_trade_outcomes'")
if not cursor.fetchone():
    print('❌ ERROR: ml_trade_outcomes table does not exist!')
    print('Please create the table first:')
    print('  1. Run migration')
    print('  2. Start trading to collect data')
    conn.close()
    exit(1)

# Check if table has data
cursor.execute('SELECT COUNT(*) FROM ml_trade_outcomes')
total_trades = cursor.fetchone()[0]

if total_trades == 0:
    print('⚠️  No trades in database yet')
    print('Start trading to collect data')
    conn.close()
    exit(0)

print(f'Total trades in DB: {total_trades:,}')

# Check if exploration_mode column exists
cursor.execute('PRAGMA table_info(ml_trade_outcomes)')
columns = [col[1] for col in cursor.fetchall()]

if 'exploration_mode' not in columns:
    print('⚠️  exploration_mode column not found')
    print('All trades treated as exploitation mode')
    conn.close()
    exit(0)

# Calculate exploration rate
result = cursor.execute('''
SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN exploration_mode = 1 THEN 1 ELSE 0 END) as exploration
FROM ml_trade_outcomes
''').fetchone()

total = result[0]
exploration = result[1] or 0

if total > 0:
    rate = (exploration / total) * 100
    print(f'\n{"="*50}')
    print(f'EXPLORATION RATE')
    print(f'{"="*50}')
    print(f'Total trades:       {total:,}')
    print(f'Exploration trades: {exploration:,}')
    print(f'Exploration rate:   {rate:.1f}%')
    print(f'{"="*50}')
    
    if rate < 25:
        print('⚠️  Low exploration rate (target: 30%)')
    elif rate > 35:
        print('⚠️  High exploration rate (target: 30%)')
    else:
        print('✅ Exploration rate is good!')
else:
    print('No trades found')

conn.close()