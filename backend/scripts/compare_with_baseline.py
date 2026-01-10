# scripts/compare_with_baseline.py
import sqlite3

def compare_results():
    # Baseline (из baseline_results.txt)
    baseline = {
        'total': 7389,
        'win_rate': 96.3,
        'total_pnl': 187.21,
        'sl_count': 36,
        'sl_avg_loss': -0.053,
        'to_count': 245,
        'to_avg_loss': -0.024
    }
    
    # Current (из mexc.db)
    conn = sqlite3.connect('mexc.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM trades WHERE status='CLOSED'")
    current_total = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT 
            COUNT(*) FILTER (WHERE exit_reason='TP') as tp_count,
            COUNT(*) FILTER (WHERE exit_reason='SL') as sl_count,
            COUNT(*) FILTER (WHERE exit_reason='TIMEOUT') as to_count,
            SUM(pnl_usd) as total_pnl,
            AVG(CASE WHEN exit_reason='SL' THEN pnl_usd END) as sl_avg,
            AVG(CASE WHEN exit_reason='TIMEOUT' THEN pnl_usd END) as to_avg
        FROM trades 
        WHERE status='CLOSED'
    """)
    
    result = cursor.fetchone()
    tp_count, sl_count, to_count, total_pnl, sl_avg, to_avg = result
    
    win_rate = (tp_count / current_total * 100) if current_total > 0 else 0
    
    print("\n" + "="*80)
    print("BASELINE vs DYNAMIC SL COMPARISON")
    print("="*80)
    
    print(f"\nTotal Trades:")
    print(f"  Baseline: {baseline['total']:,}")
    print(f"  Current:  {current_total:,}")
    
    print(f"\nWin Rate:")
    print(f"  Baseline: {baseline['win_rate']:.1f}%")
    print(f"  Current:  {win_rate:.1f}%")
    print(f"  Δ:        {win_rate - baseline['win_rate']:+.1f}%")
    
    print(f"\nTotal P&L:")
    print(f"  Baseline: ${baseline['total_pnl']:.2f}")
    print(f"  Current:  ${total_pnl:.2f}")
    print(f"  Δ:        ${total_pnl - baseline['total_pnl']:+.2f}")
    
    print(f"\nStop Loss:")
    print(f"  Count:")
    print(f"    Baseline: {baseline['sl_count']} ({baseline['sl_count']/baseline['total']*100:.1f}%)")
    print(f"    Current:  {sl_count} ({sl_count/current_total*100:.1f}%)")
    print(f"  Avg Loss:")
    print(f"    Baseline: ${baseline['sl_avg_loss']:.4f}")
    print(f"    Current:  ${sl_avg:.4f}")
    print(f"    Δ:        ${sl_avg - baseline['sl_avg_loss']:+.4f} ({(sl_avg/baseline['sl_avg_loss']-1)*100:+.1f}%)")
    
    print(f"\nTimeout:")
    print(f"  Count:")
    print(f"    Baseline: {baseline['to_count']} ({baseline['to_count']/baseline['total']*100:.1f}%)")
    print(f"    Current:  {to_count} ({to_count/current_total*100:.1f}%)")
    print(f"  Avg Loss:")
    print(f"    Baseline: ${baseline['to_avg_loss']:.4f}")
    print(f"    Current:  ${to_avg:.4f}")
    
    # Verdict
    print("\n" + "="*80)
    print("VERDICT:")
    print("="*80)
    
    sl_improvement = (baseline['sl_avg_loss'] - sl_avg) / abs(baseline['sl_avg_loss']) * 100
    
    if sl_improvement > 15:
        print("✅ EXCELLENT: SL improvement > 15%")
    elif sl_improvement > 5:
        print("🟡 GOOD: SL improvement 5-15%")
    elif sl_improvement > 0:
        print("⚠️ MARGINAL: SL improvement < 5%")
    else:
        print("❌ WORSE: SL worse than baseline")
    
    conn.close()

if __name__ == "__main__":
    compare_results()