"""
Analyze exits by symbol - TP + TRAIL + TIMEOUT + SL analysis
Uses TRADES table
Windows-compatible - no emojis
Version: 2.0 - WITH TRAILING STOP SUPPORT
"""
import sqlite3
import sys
import codecs
from pathlib import Path

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Find database
db_paths = [
    'mexc.db',
    '../mexc.db',
    'app.db',
]

db_path = None
for p in db_paths:
    if Path(p).exists():
        db_path = p
        break

if not db_path:
    print("[ERROR] Database not found!")
    sys.exit(1)

print(f"[OK] Using database: {db_path}\n")

# Connect
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Check trades table structure
print("=" * 80)
print("CHECKING TRADES TABLE")
print("=" * 80)

c.execute("PRAGMA table_info(trades)")
columns = {row[1]: row[2] for row in c.fetchall()}

if not columns:
    print("[ERROR] Trades table not found or empty!")
    sys.exit(1)

print(f"Columns: {', '.join(columns.keys())}\n")

# Count total records
c.execute("SELECT COUNT(*) FROM trades")
total_records = c.fetchone()[0]
print(f"Total records in trades: {total_records}")

# Count records in last 24 hours
c.execute("SELECT COUNT(*) FROM trades WHERE created_at > datetime('now', '-24 hours')")
recent_records = c.fetchone()[0]
print(f"Records in last 24 hours: {recent_records}\n")

if recent_records == 0:
    print("[WARNING] No trades in last 24 hours, analyzing ALL trades instead\n")
    time_filter = ""
    time_filter_where = ""
else:
    time_filter = "WHERE created_at > datetime('now', '-24 hours')"
    time_filter_where = "AND created_at > datetime('now', '-24 hours')"

# Main analysis
print("=" * 80)
print("EXIT ANALYSIS BY SYMBOL" + (" (LAST 24H)" if time_filter else " (ALL TIME)"))
print("=" * 80)

# Determine column names based on what's available
exit_col = 'exit' if 'exit' in columns else 'exit_reason'
pnl_col = 'realized_pnl_usd' if 'realized_pnl_usd' in columns else 'pnl_usd'

query = f'''
    SELECT 
        symbol,
        COUNT(*) as total_trades,
        
        -- TP count
        SUM(CASE 
            WHEN UPPER({exit_col}) = 'TP' THEN 1 
            ELSE 0 
        END) as tp_count,
        
        -- TRAIL count (NEW)
        SUM(CASE 
            WHEN UPPER({exit_col}) = 'TRAIL' THEN 1 
            WHEN UPPER({exit_col}) = 'TRAILING' THEN 1
            ELSE 0 
        END) as trail_count,
        
        -- TIMEOUT count
        SUM(CASE 
            WHEN UPPER({exit_col}) = 'TIMEOUT' THEN 1 
            WHEN UPPER({exit_col}) = 'TO' THEN 1
            ELSE 0 
        END) as timeout_count,
        
        -- SL count
        SUM(CASE 
            WHEN UPPER({exit_col}) = 'SL' THEN 1 
            WHEN UPPER({exit_col}) = 'STOP_LOSS' THEN 1
            WHEN UPPER({exit_col}) = 'STOPLOSS' THEN 1
            ELSE 0 
        END) as sl_count,
        
        -- Percentages
        ROUND(
            SUM(CASE WHEN UPPER({exit_col}) = 'TP' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 
            1
        ) as tp_pct,
        
        ROUND(
            SUM(CASE 
                WHEN UPPER({exit_col}) = 'TRAIL' OR UPPER({exit_col}) = 'TRAILING' 
                THEN 1 
                ELSE 0 
            END) * 100.0 / COUNT(*), 
            1
        ) as trail_pct,
        
        ROUND(
            SUM(CASE 
                WHEN UPPER({exit_col}) = 'TIMEOUT' THEN 1 
                WHEN UPPER({exit_col}) = 'TO' THEN 1
                ELSE 0 
            END) * 100.0 / COUNT(*), 
            1
        ) as timeout_pct,
        
        ROUND(
            SUM(CASE 
                WHEN UPPER({exit_col}) = 'SL' THEN 1 
                WHEN UPPER({exit_col}) = 'STOP_LOSS' THEN 1
                WHEN UPPER({exit_col}) = 'STOPLOSS' THEN 1
                ELSE 0 
            END) * 100.0 / COUNT(*), 
            1
        ) as sl_pct,
        
        -- Average P&L
        ROUND(AVG({pnl_col}), 4) as avg_pnl,
        
        ROUND(AVG(CASE 
            WHEN UPPER({exit_col}) = 'TP' 
            THEN {pnl_col} 
        END), 4) as avg_pnl_tp,
        
        ROUND(AVG(CASE 
            WHEN UPPER({exit_col}) IN ('TRAIL', 'TRAILING')
            THEN {pnl_col} 
        END), 4) as avg_pnl_trail,
        
        ROUND(AVG(CASE 
            WHEN UPPER({exit_col}) = 'TIMEOUT' OR UPPER({exit_col}) = 'TO' 
            THEN {pnl_col} 
        END), 4) as avg_pnl_timeout,
        
        ROUND(AVG(CASE 
            WHEN UPPER({exit_col}) IN ('SL', 'STOP_LOSS', 'STOPLOSS')
            THEN {pnl_col} 
        END), 4) as avg_pnl_sl,
        
        -- Total P&L
        ROUND(SUM({pnl_col}), 2) as total_pnl
        
    FROM trades
    {time_filter}
    GROUP BY symbol
    ORDER BY total_trades DESC
'''

try:
    c.execute(query)
    results = c.fetchall()
    
    if not results:
        print("[WARNING] No trades found")
    else:
        # Print header
        print(f"\n{'SYMBOL':<12} {'TOTAL':<6} {'TPs':<6} {'TRLs':<6} {'TOs':<6} {'SLs':<5} "
              f"{'TP%':<6} {'TR%':<6} {'TO%':<6} {'SL%':<6} {'AVG$':<8}")
        print("-" * 90)
        
        # Print results
        for row in results:
            symbol = row[0]
            total = row[1]
            tp_count = row[2]
            trail_count = row[3]
            timeout_count = row[4]
            sl_count = row[5]
            tp_pct = row[6] or 0.0
            trail_pct = row[7] or 0.0
            timeout_pct = row[8] or 0.0
            sl_pct = row[9] or 0.0
            avg_pnl = row[10] or 0.0
            
            # Overall quality marker
            fail_rate = timeout_pct + sl_pct
            if fail_rate < 5:
                marker = "[GREEN]"
            elif fail_rate < 15:
                marker = "[YELLOW]"
            else:
                marker = "[RED]"
            
            print(
                f"{marker} {symbol:<10} {total:<6} {tp_count:<6} {trail_count:<6} {timeout_count:<6} {sl_count:<5} "
                f"{tp_pct:<6.1f} {trail_pct:<6.1f} {timeout_pct:<6.1f} {sl_pct:<6.1f} ${avg_pnl:<7.4f}"
            )
        
        # Detailed breakdown per symbol
        print("\n" + "=" * 80)
        print("DETAILED BREAKDOWN BY SYMBOL")
        print("=" * 80)
        
        for row in results:
            symbol = row[0]
            total = row[1]
            tp_count = row[2]
            trail_count = row[3]
            timeout_count = row[4]
            sl_count = row[5]
            tp_pct = row[6] or 0.0
            trail_pct = row[7] or 0.0
            timeout_pct = row[8] or 0.0
            sl_pct = row[9] or 0.0
            avg_pnl = row[10] or 0.0
            avg_pnl_tp = row[11] or 0.0
            avg_pnl_trail = row[12] or 0.0
            avg_pnl_to = row[13] or 0.0
            avg_pnl_sl = row[14] or 0.0
            total_pnl = row[15] or 0.0
            
            fail_rate = timeout_pct + sl_pct
            
            if fail_rate < 5:
                quality = "EXCELLENT"
                marker = "[GREEN]"
            elif fail_rate < 15:
                quality = "GOOD"
                marker = "[YELLOW]"
            else:
                quality = "POOR"
                marker = "[RED]"
            
            print(f"\n{marker} {symbol} - {quality}")
            print(f"   Total trades: {total}")
            print(f"   [+] TP:      {tp_count:<5} ({tp_pct:>5.1f}%)  Avg: ${avg_pnl_tp or 0:.4f}")
            
            if trail_count > 0:
                print(f"   [🎯] TRAIL:  {trail_count:<5} ({trail_pct:>5.1f}%)  Avg: ${avg_pnl_trail:.4f}")
            else:
                print(f"   [🎯] TRAIL:  {trail_count:<5} ({trail_pct:>5.1f}%)  (None yet)")
            
            print(f"   [!] TIMEOUT: {timeout_count:<5} ({timeout_pct:>5.1f}%)  Avg: ${avg_pnl_to or 0:.4f}")
            
            if sl_count > 0:
                print(f"   [-] SL:      {sl_count:<5} ({sl_pct:>5.1f}%)  Avg: ${avg_pnl_sl:.4f}")
            else:
                print(f"   [-] SL:      {sl_count:<5} ({sl_pct:>5.1f}%)  (None)")
            
            print(f"   [$] Total P&L: ${total_pnl:.2f}")
    
    # Overall statistics
    print("\n" + "=" * 80)
    print("OVERALL STATISTICS")
    print("=" * 80)
    
    query2 = f'''
        SELECT 
            COUNT(*) as total_trades,
            
            SUM(CASE WHEN UPPER({exit_col}) = 'TP' THEN 1 ELSE 0 END) as total_tp,
            
            SUM(CASE 
                WHEN UPPER({exit_col}) IN ('TRAIL', 'TRAILING')
                THEN 1 
                ELSE 0 
            END) as total_trail,
            
            SUM(CASE 
                WHEN UPPER({exit_col}) = 'TIMEOUT' OR UPPER({exit_col}) = 'TO' 
                THEN 1 
                ELSE 0 
            END) as total_timeouts,
            
            SUM(CASE 
                WHEN UPPER({exit_col}) IN ('SL', 'STOP_LOSS', 'STOPLOSS')
                THEN 1 
                ELSE 0 
            END) as total_sl,
            
            ROUND(
                SUM(CASE WHEN UPPER({exit_col}) = 'TP' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 
                1
            ) as tp_pct,
            
            ROUND(
                SUM(CASE 
                    WHEN UPPER({exit_col}) IN ('TRAIL', 'TRAILING')
                    THEN 1 
                    ELSE 0 
                END) * 100.0 / COUNT(*), 
                1
            ) as trail_pct,
            
            ROUND(
                SUM(CASE 
                    WHEN UPPER({exit_col}) = 'TIMEOUT' OR UPPER({exit_col}) = 'TO' 
                    THEN 1 
                    ELSE 0 
                END) * 100.0 / COUNT(*), 
                1
            ) as timeout_pct,
            
            ROUND(
                SUM(CASE 
                    WHEN UPPER({exit_col}) IN ('SL', 'STOP_LOSS', 'STOPLOSS')
                    THEN 1 
                    ELSE 0 
                END) * 100.0 / COUNT(*), 
                1
            ) as sl_pct,
            
            ROUND(SUM({pnl_col}), 2) as total_pnl,
            ROUND(AVG({pnl_col}), 4) as avg_pnl,
            
            ROUND(
                SUM(CASE WHEN UPPER({exit_col}) = 'TP' THEN {pnl_col} ELSE 0 END), 
                2
            ) as total_pnl_tp,
            
            ROUND(
                SUM(CASE 
                    WHEN UPPER({exit_col}) IN ('TRAIL', 'TRAILING')
                    THEN {pnl_col} 
                    ELSE 0 
                END), 
                2
            ) as total_pnl_trail,
            
            ROUND(
                SUM(CASE 
                    WHEN UPPER({exit_col}) = 'TIMEOUT' OR UPPER({exit_col}) = 'TO' 
                    THEN {pnl_col} 
                    ELSE 0 
                END), 
                2
            ) as total_pnl_timeout,
            
            ROUND(
                SUM(CASE 
                    WHEN UPPER({exit_col}) IN ('SL', 'STOP_LOSS', 'STOPLOSS')
                    THEN {pnl_col} 
                    ELSE 0 
                END), 
                2
            ) as total_pnl_sl
            
        FROM trades
        {time_filter}
    '''
    
    c.execute(query2)
    stats = c.fetchone()
    
    total_trades = stats[0]
    total_tp = stats[1]
    total_trail = stats[2]
    total_timeouts = stats[3]
    total_sl = stats[4]
    tp_pct = stats[5] or 0.0
    trail_pct = stats[6] or 0.0
    timeout_pct = stats[7] or 0.0
    sl_pct = stats[8] or 0.0
    total_pnl = stats[9] or 0.0
    avg_pnl = stats[10] or 0.0
    total_pnl_tp = stats[11] or 0.0
    total_pnl_trail = stats[12] or 0.0
    total_pnl_to = stats[13] or 0.0
    total_pnl_sl = stats[14] or 0.0
    
    print(f"\nTotal Trades:    {total_trades}")
    print(f"\n[+] TPs:          {total_tp:<6} ({tp_pct:.1f}%)  Total P&L: ${total_pnl_tp:.2f}")
    
    if total_trail > 0:
        print(f"[🎯] TRAILs:      {total_trail:<6} ({trail_pct:.1f}%)  Total P&L: ${total_pnl_trail:.2f}")
    else:
        print(f"[🎯] TRAILs:      {total_trail:<6} ({trail_pct:.1f}%)  (No trailing stops yet)")
    
    print(f"[!] TIMEOUTs:     {total_timeouts:<6} ({timeout_pct:.1f}%)  Total P&L: ${total_pnl_to:.2f}")
    
    if total_sl > 0:
        print(f"[-] SLs:          {total_sl:<6} ({sl_pct:.1f}%)  Total P&L: ${total_pnl_sl:.2f}")
    else:
        print(f"[-] SLs:          {total_sl:<6} ({sl_pct:.1f}%)  (No stop losses)")
    
    print(f"\n[$] TOTAL P&L:    ${total_pnl:.2f}")
    print(f"[$] AVG P&L:      ${avg_pnl:.4f} per trade")
    
    # Win rate (TP + TRAIL)
    win_count = total_tp + total_trail
    win_rate = win_count / total_trades * 100 if total_trades > 0 else 0
    print(f"[%] WIN RATE:     {win_rate:.1f}% (TP + TRAIL)")
    
    # Fail rate
    fail_rate = timeout_pct + sl_pct
    print(f"[%] FAIL RATE:    {fail_rate:.1f}% (TIMEOUT + SL)")
    
    # TRAILING STOP ANALYSIS (NEW SECTION)
    if total_trail > 0:
        print("\n" + "=" * 80)
        print("TRAILING STOP PERFORMANCE")
        print("=" * 80)
        
        # Get detailed trail stats
        c.execute(f'''
            SELECT 
                COUNT(*) as trail_count,
                ROUND(AVG({pnl_col}), 4) as avg_pnl_usd,
                ROUND(AVG(pnl_bps), 2) as avg_pnl_bps,
                ROUND(AVG(hold_duration_sec), 1) as avg_duration_sec,
                ROUND(MAX(pnl_bps), 2) as max_pnl_bps,
                ROUND(MIN(pnl_bps), 2) as min_pnl_bps
            FROM trades
            WHERE UPPER({exit_col}) IN ('TRAIL', 'TRAILING')
            {time_filter_where}
        ''')
        
        trail_stats = c.fetchone()
        if trail_stats and trail_stats[0] > 0:
            trail_cnt, trail_avg_usd, trail_avg_bps, trail_avg_dur, trail_max_bps, trail_min_bps = trail_stats
            
            print(f"\nTotal TRAIL exits:     {trail_cnt}")
            print(f"Avg P&L (USD):         ${trail_avg_usd:.4f}")
            print(f"Avg P&L (bps):         {trail_avg_bps:.2f} bps")
            print(f"Avg Duration:          {trail_avg_dur:.1f}s")
            print(f"Best TRAIL:            {trail_max_bps:.2f} bps")
            print(f"Worst TRAIL:           {trail_min_bps:.2f} bps")
            
            # Compare TRAIL vs TP
            c.execute(f'''
                SELECT 
                    ROUND(AVG({pnl_col}), 4) as avg_pnl_usd,
                    ROUND(AVG(pnl_bps), 2) as avg_pnl_bps,
                    ROUND(AVG(hold_duration_sec), 1) as avg_duration_sec
                FROM trades
                WHERE UPPER({exit_col}) = 'TP'
                {time_filter_where}
            ''')
            
            tp_stats = c.fetchone()
            if tp_stats:
                tp_avg_usd, tp_avg_bps, tp_avg_dur = tp_stats
                
                print("\n" + "-" * 80)
                print("[COMPARISON] TRAIL vs TP:")
                print(f"  TRAIL:  ${trail_avg_usd:.4f} ({trail_avg_bps:.2f} bps), {trail_avg_dur:.1f}s hold")
                print(f"  TP:     ${tp_avg_usd:.4f} ({tp_avg_bps:.2f} bps), {tp_avg_dur:.1f}s hold")
                
                if tp_avg_bps and tp_avg_bps > 0:
                    improvement_pct = ((trail_avg_bps - tp_avg_bps) / tp_avg_bps * 100)
                    improvement_usd = trail_avg_usd - tp_avg_usd
                    
                    print(f"\n  Improvement: {improvement_pct:+.1f}% in profit per trade")
                    print(f"  Worth an extra ${improvement_usd:+.4f} per trade")
                    print(f"  Holding {trail_avg_dur - tp_avg_dur:+.1f}s longer on average")
                    
                    # Projected improvement
                    if total_trades > 0:
                        projected_extra = improvement_usd * total_trail
                        print(f"\n[IMPACT] For {trail_cnt} TRAIL exits:")
                        print(f"  Extra profit captured: ${projected_extra:+.2f}")
                        print(f"  vs if they were normal TP exits: ${trail_cnt * tp_avg_usd:.2f}")
                
                print("\n[IDEA] INSIGHT:")
                if trail_avg_bps > tp_avg_bps + 0.5:
                    print(f"   Trailing Stop is capturing an extra {trail_avg_bps - tp_avg_bps:.2f} bps on average!")
                    print(f"   This is a {improvement_pct:.1f}% improvement over regular TP exits")
                else:
                    print(f"   Trailing Stop is working but improvement is modest")
                    print(f"   Consider: more aggressive trail settings or higher timeout")
    else:
        print("\n" + "=" * 80)
        print("TRAILING STOP PERFORMANCE")
        print("=" * 80)
        print("\n[!] No TRAIL exits yet")
        print("   Trailing Stop may be activating but not triggering exits")
        print("   Check logs for '🎯 Trailing Stop ACTIVATED' messages")
        print("   May need:")
        print("     - Longer timeout (allow positions to develop)")
        print("     - Wider trailing_stop_bps (less aggressive)")
        print("     - More volatile markets")
    
    # Problem symbols analysis (keeping original)
    print("\n" + "=" * 80)
    print("PROBLEM SYMBOLS ANALYSIS")
    print("=" * 80)
    
    query3 = f'''
        SELECT 
            symbol,
            COUNT(*) as total,
            
            SUM(CASE 
                WHEN UPPER({exit_col}) = 'TIMEOUT' OR UPPER({exit_col}) = 'TO' 
                THEN 1 
                ELSE 0 
            END) as timeouts,
            
            SUM(CASE 
                WHEN UPPER({exit_col}) IN ('SL', 'STOP_LOSS', 'STOPLOSS')
                THEN 1 
                ELSE 0 
            END) as sl_count,
            
            ROUND(
                SUM(CASE 
                    WHEN UPPER({exit_col}) = 'TIMEOUT' OR UPPER({exit_col}) = 'TO' 
                    THEN 1 
                    ELSE 0 
                END) * 100.0 / COUNT(*), 
                1
            ) as timeout_pct,
            
            ROUND(
                SUM(CASE 
                    WHEN UPPER({exit_col}) IN ('SL', 'STOP_LOSS', 'STOPLOSS')
                    THEN 1 
                    ELSE 0 
                END) * 100.0 / COUNT(*), 
                1
            ) as sl_pct
            
        FROM trades
        {time_filter}
        GROUP BY symbol
        HAVING (timeout_pct + sl_pct) > 15 AND total > 20
        ORDER BY (timeout_pct + sl_pct) DESC
    '''
    
    c.execute(query3)
    problem_symbols = c.fetchall()
    
    if problem_symbols:
        print("\n[!] SYMBOLS WITH HIGH FAIL RATE (>15% TIMEOUT+SL):\n")
        
        for symbol, total, timeouts, sl_count, timeout_pct, sl_pct in problem_symbols:
            fail_rate = timeout_pct + sl_pct
            print(f"  [RED] {symbol}:")
            print(f"     Total: {total} trades, Fail rate: {fail_rate:.1f}%")
            print(f"     - TIMEOUT: {timeouts} ({timeout_pct:.1f}%)")
            print(f"     - SL:      {sl_count} ({sl_pct:.1f}%)")
            print()
        
        print("[LIST] RECOMMENDED ACTIONS:")
        print("  1. Add to blacklist temporarily")
        print("  2. Wait for ML to filter these automatically")
        print("  3. Or increase TP/timeout specifically for these symbols")
        
        blacklist_symbols = [s[0] for s in problem_symbols]
        
        print("\n[CODE] CODE TO ADD TO engine.py:")
        print("=" * 80)
        print("# Near top of app/strategy/engine.py")
        print()
        print("SYMBOL_BLACKLIST = {")
        print("    'ATOMUSDT',  # Already blacklisted")
        for sym in blacklist_symbols:
            print(f"    '{sym}',  # High fail rate (TIMEOUT+SL)")
        print("}")
        print("=" * 80)
        
    else:
        print("[OK] No symbols with excessive fail rate (>15%)")
        print("   All symbols performing within acceptable range")
    
    # SL-specific analysis (keeping original)
    if total_sl > 0:
        print("\n" + "=" * 80)
        print("STOP LOSS ANALYSIS")
        print("=" * 80)
        
        if time_filter_where:
            sl_time_filter = f"WHERE UPPER({exit_col}) IN ('SL', 'STOP_LOSS', 'STOPLOSS') {time_filter_where}"
        else:
            sl_time_filter = f"WHERE UPPER({exit_col}) IN ('SL', 'STOP_LOSS', 'STOPLOSS')"
        
        query4 = f'''
            SELECT 
                symbol,
                COUNT(*) as sl_count,
                ROUND(AVG({pnl_col}), 4) as avg_loss,
                ROUND(SUM({pnl_col}), 2) as total_loss
            FROM trades
            {sl_time_filter}
            GROUP BY symbol
            ORDER BY sl_count DESC
        '''
        
        c.execute(query4)
        sl_details = c.fetchall()
        
        if sl_details:
            print(f"\nSymbols with Stop Loss exits:\n")
            print(f"{'SYMBOL':<12} {'SL COUNT':<10} {'AVG LOSS':<12} {'TOTAL LOSS':<12}")
            print("-" * 50)
            
            for symbol, sl_count, avg_loss, total_loss in sl_details:
                print(f"[-] {symbol:<10} {sl_count:<10} ${avg_loss:<11.4f} ${total_loss:<11.2f}")
            
            print(f"\n[IDEA] INSIGHT:")
            print(f"   Stop losses indicate dangerous market conditions")
            print(f"   ML will learn to avoid these patterns")
    
    # Additional insights (keeping original)
    print("\n" + "=" * 80)
    print("INSIGHTS & RECOMMENDATIONS")
    print("=" * 80)
    
    fail_rate_overall = timeout_pct + sl_pct
    
    if fail_rate_overall > 20:
        print("[RED] Overall fail rate is HIGH (>20%)")
        print("   URGENT actions needed:")
        print("   - Increase TP from 2 bps to 5 bps globally")
        print("   - Increase timeout from 30s to 45s")
        print("   - Review entry filters (spread, depth, imbalance)")
        print("   - ML filter is CRITICAL for this dataset")
    elif fail_rate_overall > 10:
        print("[YELLOW] Fail rate is MODERATE (10-20%)")
        print("   Recommended actions:")
        print("   - Consider slight TP increase (2 -> 3-4 bps)")
        print("   - ML filter will help reduce this significantly")
    else:
        print("[OK] Fail rate is LOW (<10%)")
        print("   Current settings are working well!")
        if total_trail > 0:
            print("   Trailing Stop is adding extra profit on top!")
        else:
            print("   Consider enabling Trailing Stop to capture more profit")
    
    if timeout_pct > sl_pct * 3:
        print(f"\n[!] TIMEOUT-dominant failures ({timeout_pct:.1f}% TO vs {sl_pct:.1f}% SL)")
        print("   Problem: Slow markets, not reaching TP in time")
        print("   Solutions:")
        print("   - Increase timeout duration")
        print("   - Decrease TP target")
        print("   - Filter low-volume pairs")
        if total_trail > 0:
            print("   - Trailing Stop may help here by capturing partial profits")
    elif sl_pct > 3:
        print(f"\n[-] HIGH STOP LOSS RATE ({sl_pct:.1f}%)")
        print("   Problem: Market moving against positions")
        print("   Solutions:")
        print("   - Tighten entry filters (especially imbalance)")
        print("   - Widen stop loss")
        print("   - Avoid extreme market conditions")
    
    # ML readiness (keeping original)
    print("\n" + "=" * 80)
    print("ML TRAINING READINESS")
    print("=" * 80)
    
    positive_examples = total_tp + total_trail  # Include TRAIL as positive
    negative_examples = total_timeouts + total_sl
    
    if negative_examples == 0:
        print("[-] NO NEGATIVE EXAMPLES!")
        print("   Cannot train ML without failures")
        print("   Continue collecting data")
    elif negative_examples < positive_examples * 0.1:
        print("[!] Too few negative examples (<10% of positives)")
        print("   ML may not learn to avoid failures effectively")
        print("   Recommendation: Collect more data or relax filters temporarily")
    elif negative_examples > positive_examples * 0.5:
        print("[!] Too many negative examples (>50% of positives)")
        print("   System struggling, fix entry logic first")
    else:
        print("[OK] GOOD BALANCE FOR ML TRAINING!")
        print(f"   Positive (TP+TRAIL): {positive_examples} ({(tp_pct+trail_pct):.1f}%)")
        print(f"   Negative (TO+SL):    {negative_examples} ({fail_rate_overall:.1f}%)")
        print(f"   Ratio: {positive_examples/negative_examples:.2f}:1")
        print("\n   Ready to proceed with ML training!")

except Exception as e:
    print(f"[-] Query error: {e}")
    import traceback
    traceback.print_exc()

conn.close()

print("\n" + "=" * 80)
print("[OK] Analysis complete!")
print("=" * 80)
print("\nFiles to run next:")
print("  - python scripts/ml_export_with_labels.py  (export data)")
print("  - python scripts/ml_train_v5.py            (train model)")
