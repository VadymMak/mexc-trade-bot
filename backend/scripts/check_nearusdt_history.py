import sqlite3
from datetime import datetime

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

print("\n" + "="*70)
print("NEARUSDT P&L HISTORY BY DAY")
print("="*70)

# P&L по дням для NEARUSDT
cursor.execute('''
    SELECT 
        DATE(entry_time) as day,
        COUNT(*) as trades,
        SUM(CASE WHEN exit_reason = 'TP' THEN 1 ELSE 0 END) as tp_count,
        SUM(CASE WHEN exit_reason IN ('SL', 'TIMEOUT') THEN 1 ELSE 0 END) as fail_count,
        SUM(pnl_usd) as daily_pnl,
        AVG(pnl_usd) as avg_pnl
    FROM trades
    WHERE symbol = 'NEARUSDT'
      AND exit_reason IS NOT NULL
    GROUP BY DATE(entry_time)
    ORDER BY day DESC
    LIMIT 10
''')

rows = cursor.fetchall()

print(f"\n{'Date':12s} {'Trades':>7s} {'TP':>5s} {'Fail':>5s} {'WR%':>6s} {'Daily P&L':>12s} {'Avg':>8s}")
print("-" * 70)

total_pnl = 0
for row in rows:
    day, trades, tp, fail, daily_pnl, avg_pnl = row
    wr = (tp / trades * 100) if trades > 0 else 0
    total_pnl += daily_pnl
    
    # Color
    if daily_pnl > 10:
        color = "✅"
    elif daily_pnl > 0:
        color = "🟢"
    elif daily_pnl > -10:
        color = "🟡"
    else:
        color = "🔴"
    
    print(f"{day:12s} {trades:7,d} {tp:5,d} {fail:5,d} {wr:5.1f}% {color} ${daily_pnl:10.2f}  ${avg_pnl:7.4f}")

print("-" * 70)
print(f"{'TOTAL':12s} {' ':7s} {' ':5s} {' ':5s} {' ':6s}    ${total_pnl:10.2f}")

# Cumulative P&L
cursor.execute('''
    SELECT SUM(pnl_usd) 
    FROM trades 
    WHERE symbol = 'NEARUSDT' 
      AND exit_reason IS NOT NULL
''')
cumulative = cursor.fetchone()[0] or 0

print("\n" + "="*70)
print(f"NEARUSDT Cumulative P&L (all time): ${cumulative:.2f}")
print("="*70)

conn.close()