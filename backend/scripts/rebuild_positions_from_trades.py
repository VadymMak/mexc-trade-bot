import sqlite3
from datetime import datetime

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

print("\n" + "="*70)
print("REBUILDING POSITIONS FROM TRADES")
print("="*70)

# 1. Очистить positions таблицу
print("\n[1/3] Clearing positions table...")
cursor.execute("DELETE FROM positions")
conn.commit()
print("   ✅ Positions cleared")

# 2. Пересоздать positions из trades
print("\n[2/3] Calculating P&L from trades...")

symbols = ['NEARUSDT', 'LINKUSDT', 'VETUSDT', 'ALGOUSDT', 'AVAXUSDT']
now = datetime.now()

for symbol in symbols:
    # Считаем realized P&L из завершённых trades
    cursor.execute("""
        SELECT 
            COALESCE(SUM(pnl_usd), 0) as realized_pnl,
            COUNT(*) as trade_count
        FROM trades
        WHERE symbol = ?
          AND exit_reason IS NOT NULL
    """, (symbol,))
    
    row = cursor.fetchone()
    realized_pnl = row[0] if row else 0.0
    trade_count = row[1] if row else 0
    
    # Проверяем открытую позицию (если есть)
    cursor.execute("""
        SELECT 
            entry_qty,
            entry_price
        FROM trades
        WHERE symbol = ?
          AND exit_reason IS NULL
        ORDER BY entry_time DESC
        LIMIT 1
    """, (symbol,))
    
    open_row = cursor.fetchone()
    
    if open_row:
        qty = open_row[0] or 0.0
        entry_price = open_row[1] or 0.0
        is_open = 1
        status = 'OPEN'
        closed_at = None
    else:
        qty = 0.0
        entry_price = 0.0
        is_open = 0
        status = 'CLOSED'
        closed_at = now
    
    # Вставить в positions
    cursor.execute("""
        INSERT INTO positions 
        (workspace_id, symbol, side, qty, entry_price, realized_pnl, unrealized_pnl,
         status, is_open, opened_at, closed_at, created_at, updated_at, revision)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        1,                  # workspace_id
        symbol,             # symbol
        'BUY',              # side
        qty,                # qty
        entry_price,        # entry_price
        realized_pnl,       # realized_pnl
        0.0,                # unrealized_pnl
        status,             # status
        is_open,            # is_open
        now,                # opened_at (всегда now)
        closed_at,          # closed_at (None если открыта)
        now,                # created_at
        now,                # updated_at
        1                   # revision
    ))
    
    print(f"   {symbol:10s} qty={qty:.4f}  entry=${entry_price:.4f}  P&L=${realized_pnl:.2f}  ({trade_count} trades)")

conn.commit()

# 3. Проверка
print("\n[3/3] Verification...")

cursor.execute("""
    SELECT 
        p.symbol,
        p.realized_pnl as pos_pnl,
        COALESCE((
            SELECT SUM(t.pnl_usd)
            FROM trades t
            WHERE t.symbol = p.symbol 
              AND t.exit_reason IS NOT NULL
        ), 0) as trades_pnl
    FROM positions p
""")

print(f"\n{'Symbol':12s} {'Pos P&L':>12s} {'Trades P&L':>12s} {'Match':>8s}")
print("-" * 50)

all_match = True
for row in cursor.fetchall():
    symbol, pos_pnl, trades_pnl = row
    match = "✅" if abs(pos_pnl - trades_pnl) < 0.01 else "❌"
    if match == "❌":
        all_match = False
    print(f"{symbol:12s} ${pos_pnl:11.2f} ${trades_pnl:11.2f} {match:>8s}")

print("\n" + "="*70)
if all_match:
    print("✅ SUCCESS! Positions rebuilt correctly!")
    print("\nNow restart backend and check:")
    print("  curl http://localhost:8000/api/exec/positions")
else:
    print("⚠️  Some mismatches - check data")
print("="*70)

conn.close()