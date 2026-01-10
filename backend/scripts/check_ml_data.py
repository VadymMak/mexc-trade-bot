import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else 'mexc.db'

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 80)
print("🔍 CHECKING ML_TRADE_OUTCOMES DATA")
print("=" * 80)

cursor.execute("""
    SELECT 
        symbol, 
        entry_time, 
        exit_reason,
        spread_bps_entry, 
        imbalance_entry,
        depth5_bid_usd_entry, 
        depth5_ask_usd_entry,
        trades_per_min_entry, 
        usd_per_min_entry,
        atr1m_pct_entry,
        grinder_ratio_entry,
        pnl_bps,
        hold_duration_sec
    FROM ml_trade_outcomes 
    ORDER BY id DESC 
    LIMIT 5
""")

rows = cursor.fetchall()

if not rows:
    print("❌ NO DATA FOUND!")
else:
    for i, row in enumerate(rows, 1):
        print(f"\n📊 TRADE #{i}:")
        print(f"  Symbol:              {row[0]}")
        print(f"  Entry Time:          {row[1]}")
        print(f"  Exit Reason:         {row[2]}")
        print(f"  Spread BPS:          {row[3]:.2f}")
        print(f"  Imbalance:           {row[4]:.3f}")
        print(f"  Depth5 Bid USD:      ${row[5]:.2f}")
        print(f"  Depth5 Ask USD:      ${row[6]:.2f}")
        print(f"  Trades/min:          {row[7]:.2f}")
        print(f"  USD/min:             ${row[8]:.2f}")
        print(f"  ATR 1m %:            {row[9]:.4f}")
        print(f"  Grinder Ratio:       {row[10]:.4f}")
        print(f"  PnL BPS:             {row[11]:.2f}")
        print(f"  Hold Duration:       {row[12]:.1f}s")
        
        # Check if features are NOT zero
        non_zero_features = 0
        if row[3] > 0: non_zero_features += 1  # spread
        if row[4] > 0: non_zero_features += 1  # imbalance
        if row[5] > 0: non_zero_features += 1  # depth5_bid
        if row[6] > 0: non_zero_features += 1  # depth5_ask
        
        print(f"  ✅ Non-zero features: {non_zero_features}/4 (spread, imb, depth5)")

print("\n" + "=" * 80)
print(f"✅ TOTAL TRADES LOGGED: {len(rows)}")
print("=" * 80)

conn.close()