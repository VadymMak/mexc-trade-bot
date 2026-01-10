import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

print("\n" + "="*70)
print("POSITIONS TABLE vs FILLS CALCULATION")
print("="*70)

symbols = ['NEARUSDT', 'LINKUSDT', 'VETUSDT', 'ALGOUSDT', 'AVAXUSDT']

print(f"\n{'Symbol':12s} {'Pos.realized':>14s} {'Fills calc':>14s} {'Diff':>14s}")
print("-" * 70)

for symbol in symbols:
    # P&L из positions таблицы
    cursor.execute("""
        SELECT realized_pnl 
        FROM positions 
        WHERE symbol = ?
    """, (symbol,))
    
    row = cursor.fetchone()
    pos_pnl = row[0] if row else 0.0
    
    # P&L из fills (расчёт вручную)
    cursor.execute("""
        SELECT 
            side,
            SUM(qty) as total_qty,
            SUM(qty * price) as total_value,
            SUM(fee) as total_fee
        FROM fills
        WHERE symbol = ?
        GROUP BY side
    """, (symbol,))
    
    buys_qty = buys_value = buys_fee = 0.0
    sells_qty = sells_value = sells_fee = 0.0
    
    for row in cursor.fetchall():
        side, qty, value, fee = row
        if side == 'BUY':
            buys_qty = qty or 0
            buys_value = value or 0
            buys_fee = fee or 0
        else:
            sells_qty = qty or 0
            sells_value = value or 0
            sells_fee = fee or 0
    
    # Упрощённый расчёт (предполагая flat в конце)
    fills_pnl = sells_value - (sells_qty / buys_qty * buys_value if buys_qty > 0 else 0) - buys_fee - sells_fee
    
    diff = pos_pnl - fills_pnl
    
    status = "✅" if abs(diff) < 10 else "❌"
    print(f"{symbol:12s} ${pos_pnl:13.2f} ${fills_pnl:13.2f} ${diff:13.2f} {status}")

# Теперь сравним с trades P&L
print("\n" + "="*70)
print("POSITIONS vs TRADES P&L")
print("="*70)

print(f"\n{'Symbol':12s} {'Pos.realized':>14s} {'Trades P&L':>14s} {'Diff':>14s}")
print("-" * 70)

for symbol in symbols:
    # P&L из positions
    cursor.execute("""
        SELECT realized_pnl 
        FROM positions 
        WHERE symbol = ?
    """, (symbol,))
    
    row = cursor.fetchone()
    pos_pnl = row[0] if row else 0.0
    
    # P&L из trades
    cursor.execute("""
        SELECT SUM(pnl_usd)
        FROM trades
        WHERE symbol = ?
          AND exit_reason IS NOT NULL
    """, (symbol,))
    
    row = cursor.fetchone()
    trades_pnl = row[0] if row and row[0] else 0.0
    
    diff = pos_pnl - trades_pnl
    
    status = "✅" if abs(diff) < 10 else "❌"
    print(f"{symbol:12s} ${pos_pnl:13.2f} ${trades_pnl:13.2f} ${diff:13.2f} {status}")

print("\n" + "="*70)

# Проверим workspace_id
cursor.execute("SELECT DISTINCT workspace_id FROM positions")
pos_workspaces = [r[0] for r in cursor.fetchall()]

cursor.execute("SELECT DISTINCT workspace_id FROM fills")
fills_workspaces = [r[0] for r in cursor.fetchall()]

print("\nWorkspace IDs:")
print(f"  Positions: {pos_workspaces}")
print(f"  Fills:     {fills_workspaces}")

if pos_workspaces != fills_workspaces:
    print("\n⚠️  WORKSPACE MISMATCH!")
    print("  Positions and Fills have different workspace_id!")

print("="*70)

conn.close()