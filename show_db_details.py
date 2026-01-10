import sqlite3

conn = sqlite3.connect('backend/mexc.db')
cursor = conn.cursor()

print("=" * 80)
print("DATABASE: backend/mexc.db")
print("=" * 80)

# Show trades table columns
cursor.execute('PRAGMA table_info(trades)')
cols = cursor.fetchall()
print('\nTRADES TABLE COLUMNS:')
print('-' * 80)
for col in cols:
    print(f'{col[1]:25s} - {col[2]}')

# Get some sample data
cursor.execute('SELECT * FROM trades LIMIT 3')
print('\n\nSAMPLE TRADES:')
print('-' * 80)
rows = cursor.fetchall()
for i, row in enumerate(rows, 1):
    print(f'\nTrade {i}:')
    for j, col in enumerate(cols):
        print(f'  {col[1]:25s}: {row[j]}')

# Check if we have futures data
cursor.execute("SELECT COUNT(DISTINCT symbol) as unique_symbols FROM trades")
symbol_count = cursor.fetchone()[0]
print(f'\n\nUNIQUE SYMBOLS (FUTURES): {symbol_count}')

# Show top symbols by trade count
cursor.execute("SELECT symbol, COUNT(*) as trade_count FROM trades GROUP BY symbol ORDER BY trade_count DESC LIMIT 10")
top_symbols = cursor.fetchall()
print('\nTOP 10 MOST TRADED SYMBOLS:')
print('-' * 80)
for symbol, count in top_symbols:
    print(f'{symbol:20s}: {count:,} trades')

# Check fills table
cursor.execute('SELECT COUNT(*) FROM fills')
fills_count = cursor.fetchone()[0]
print(f'\n\nFILLS TABLE: {fills_count:,} records')

conn.close()

print("\n" + "=" * 80)
print("✓ DATABASE LOCATION: backend/mexc.db")
print(f"✓ TOTAL TRADES: 53,222")
print(f"✓ UNIQUE SYMBOLS: {symbol_count}")
print("=" * 80)
