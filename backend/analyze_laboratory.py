"""
Analyze Laboratory Database
===========================

Analyze slot_laboratory.db to check trading results.

Usage:
    python analyze_laboratory.py
"""

import sqlite3
from datetime import datetime
from pathlib import Path


def analyze_laboratory_db(db_path="slot_laboratory.db"):
    """Analyze laboratory database."""
    
    if not Path(db_path).exists():
        print(f"❌ Database not found: {db_path}")
        print("⚠️  Make sure test_slot_laboratory.py is running!")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("\n" + "="*70)
    print("LABORATORY DATABASE ANALYSIS")
    print("="*70)
    print(f"Database: {db_path}")
    print(f"Analyzed at: {datetime.now()}")
    print("="*70 + "\n")
    
    # ===================================================================
    # 1. TOTAL TRADES
    # ===================================================================
    
    cursor.execute("""
        SELECT COUNT(*) 
        FROM ml_trade_outcomes
    """)
    total_trades = cursor.fetchone()[0]
    
    print(f"📊 TOTAL TRADES: {total_trades}")
    
    if total_trades == 0:
        print("\n⚠️  No trades logged yet!")
        print("   Wait for bot to open and close some positions.")
        conn.close()
        return
    
    # ===================================================================
    # 2. COMPLETED TRADES (with exit)
    # ===================================================================
    
    cursor.execute("""
        SELECT 
            COUNT(*) as completed,
            SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN win=0 THEN 1 ELSE 0 END) as losses,
            ROUND(AVG(pnl_bps), 2) as avg_pnl_bps,
            ROUND(AVG(pnl_usd), 4) as avg_pnl_usd,
            ROUND(AVG(hold_duration_sec), 1) as avg_hold_sec,
            ROUND(MIN(pnl_bps), 2) as min_pnl_bps,
            ROUND(MAX(pnl_bps), 2) as max_pnl_bps
        FROM ml_trade_outcomes
        WHERE exit_time IS NOT NULL
    """)
    
    row = cursor.fetchone()
    completed, wins, losses, avg_pnl_bps, avg_pnl_usd, avg_hold_sec, min_pnl_bps, max_pnl_bps = row
    
    print(f"\n📈 COMPLETED TRADES: {completed}")
    
    if completed == 0:
        print("   No completed trades yet (positions still open)")
        conn.close()
        return
    
    win_rate = (wins / completed * 100) if completed > 0 else 0
    
    print(f"   ✅ Wins: {wins}")
    print(f"   ❌ Losses: {losses}")
    print(f"   📊 Win Rate: {win_rate:.1f}%")
    print(f"   💰 Avg P&L: {avg_pnl_bps:+.2f} bps (${avg_pnl_usd:+.4f})")
    print(f"   ⏱️  Avg Hold: {avg_hold_sec:.1f}s")
    print(f"   📉 Min P&L: {min_pnl_bps:+.2f} bps")
    print(f"   📈 Max P&L: {max_pnl_bps:+.2f} bps")
    
    # ===================================================================
    # 3. EXIT REASONS
    # ===================================================================
    
    cursor.execute("""
        SELECT 
            exit_reason,
            COUNT(*) as count,
            ROUND(AVG(pnl_bps), 2) as avg_pnl_bps
        FROM ml_trade_outcomes
        WHERE exit_time IS NOT NULL
        GROUP BY exit_reason
        ORDER BY count DESC
    """)
    
    print(f"\n🎯 EXIT REASONS:")
    for row in cursor.fetchall():
        reason, count, avg_pnl = row
        pct = (count / completed * 100) if completed > 0 else 0
        print(f"   {reason:10s}: {count:4d} ({pct:5.1f}%)  Avg: {avg_pnl:+.2f} bps")
    
    # ===================================================================
    # 4. BY SYMBOL
    # ===================================================================
    
    cursor.execute("""
        SELECT 
            symbol,
            COUNT(*) as trades,
            SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins,
            ROUND(AVG(pnl_bps), 2) as avg_pnl_bps
        FROM ml_trade_outcomes
        WHERE exit_time IS NOT NULL
        GROUP BY symbol
        ORDER BY trades DESC
    """)
    
    print(f"\n📊 BY SYMBOL:")
    for row in cursor.fetchall():
        symbol, trades, sym_wins, avg_pnl = row
        wr = (sym_wins / trades * 100) if trades > 0 else 0
        print(f"   {symbol:10s}: {trades:4d} trades  WR: {wr:5.1f}%  Avg: {avg_pnl:+.2f} bps")
    
    # ===================================================================
    # 5. RECENT TRADES
    # ===================================================================
    
    cursor.execute("""
        SELECT 
            symbol,
            entry_time,
            exit_time,
            exit_reason,
            ROUND(pnl_bps, 2) as pnl_bps,
            ROUND(hold_duration_sec, 1) as hold_sec,
            win
        FROM ml_trade_outcomes
        WHERE exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 10
    """)
    
    print(f"\n📜 LAST 10 TRADES:")
    print(f"   {'Symbol':<10s} {'Entry':<19s} {'Exit':<19s} {'Reason':<8s} {'P&L (bps)':<10s} {'Hold(s)':<8s} {'Win':<4s}")
    print(f"   {'-'*80}")
    
    for row in cursor.fetchall():
        symbol, entry_time, exit_time, reason, pnl_bps, hold_sec, win = row
        entry_str = entry_time[:19] if entry_time else "N/A"
        exit_str = exit_time[:19] if exit_time else "N/A"
        win_str = "✅" if win else "❌"
        print(f"   {symbol:<10s} {entry_str:<19s} {exit_str:<19s} {reason:<8s} {pnl_bps:+10.2f} {hold_sec:<8.1f} {win_str:<4s}")
    
    # ===================================================================
    # 6. OPEN POSITIONS
    # ===================================================================
    
    cursor.execute("""
        SELECT COUNT(*)
        FROM ml_trade_outcomes
        WHERE exit_time IS NULL
    """)
    open_count = cursor.fetchone()[0]
    
    if open_count > 0:
        print(f"\n🔓 OPEN POSITIONS: {open_count}")
        
        cursor.execute("""
            SELECT 
                symbol,
                entry_time,
                ROUND(entry_price, 6) as entry_price
            FROM ml_trade_outcomes
            WHERE exit_time IS NULL
            ORDER BY entry_time DESC
        """)
        
        print(f"   {'Symbol':<10s} {'Entry Time':<19s} {'Entry Price':<12s}")
        print(f"   {'-'*45}")
        
        for row in cursor.fetchall():
            symbol, entry_time, entry_price = row
            entry_str = entry_time[:19] if entry_time else "N/A"
            print(f"   {symbol:<10s} {entry_str:<19s} {entry_price:<12.6f}")
    
    # ===================================================================
    # 7. SUMMARY & VERDICT
    # ===================================================================
    
    print(f"\n" + "="*70)
    print("VERDICT")
    print("="*70)
    
    if completed < 50:
        print(f"⏳ NOT ENOUGH DATA: {completed} trades")
        print(f"   Need at least 50 trades for meaningful analysis.")
        print(f"   Current: {completed}/50")
    
    elif win_rate >= 55 and avg_pnl_bps > 0.1:
        print(f"✅ STRATEGY LOOKS GOOD!")
        print(f"   Win Rate: {win_rate:.1f}% (target: >55%)")
        print(f"   Avg P&L: {avg_pnl_bps:+.2f} bps (target: >+0.1)")
        print(f"   ✅ Consider integrating into main engine!")
    
    elif win_rate >= 50:
        print(f"⚠️  STRATEGY IS MARGINAL")
        print(f"   Win Rate: {win_rate:.1f}% (target: >55%)")
        print(f"   Avg P&L: {avg_pnl_bps:+.2f} bps (target: >+0.1)")
        print(f"   ⏳ Need more data or parameter tuning")
    
    else:
        print(f"❌ STRATEGY NOT PROFITABLE")
        print(f"   Win Rate: {win_rate:.1f}% (target: >55%)")
        print(f"   Avg P&L: {avg_pnl_bps:+.2f} bps (target: >+0.1)")
        print(f"   ❌ Need significant changes")
    
    print("="*70 + "\n")
    
    conn.close()


if __name__ == "__main__":
    analyze_laboratory_db()