"""
Анализ результатов ML теста
Сравнивает ML phase с baseline
БЕЗ ЭМОДЗИ - для Windows консоли
"""
import sqlite3
import json
import sys
from datetime import datetime
from pathlib import Path

# Установить кодировку UTF-8 для вывода
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

# ═══════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════════════

ML_DB = "mexc.db"
BASELINE_DB = "mexc_baseline_291trades.db"
OUTPUT_FILE = f"ml_test_analysis_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

# ═══════════════════════════════════════════════════════════════════════
# ФУНКЦИИ АНАЛИЗА
# ═══════════════════════════════════════════════════════════════════════

def get_trades_stats(db_path):
    """Получить статистику из БД"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute("""
        SELECT 
            COUNT(*) as total_trades,
            SUM(CASE WHEN exit_reason = 'TP' THEN 1 ELSE 0 END) as tp_count,
            SUM(CASE WHEN exit_reason = 'TIMEOUT' THEN 1 ELSE 0 END) as timeout_count,
            SUM(CASE WHEN exit_reason = 'SL' THEN 1 ELSE 0 END) as sl_count,
            SUM(pnl_usd) as total_pnl,
            AVG(pnl_usd) as avg_pnl,
            AVG(duration_seconds) as avg_duration,
            MAX(pnl_usd) as best_trade,
            MIN(pnl_usd) as worst_trade
        FROM trades
        WHERE status = 'CLOSED'
    """)
    
    stats = cursor.fetchone()
    
    # Статистика по символам
    cursor.execute("""
        SELECT 
            symbol,
            COUNT(*) as trades,
            SUM(CASE WHEN exit_reason = 'TP' THEN 1 ELSE 0 END) as tp_count,
            SUM(CASE WHEN exit_reason = 'TIMEOUT' THEN 1 ELSE 0 END) as timeout_count,
            SUM(pnl_usd) as total_pnl,
            AVG(pnl_usd) as avg_pnl,
            AVG(duration_seconds) as avg_duration
        FROM trades
        WHERE status = 'CLOSED'
        GROUP BY symbol
        ORDER BY trades DESC
    """)
    
    per_symbol = cursor.fetchall()
    
    conn.close()
    
    return {
        'total': {
            'trades': stats[0] or 0,
            'tp': stats[1] or 0,
            'timeout': stats[2] or 0,
            'sl': stats[3] or 0,
            'total_pnl': stats[4] or 0,
            'avg_pnl': stats[5] or 0,
            'avg_duration': stats[6] or 0,
            'best_trade': stats[7] or 0,
            'worst_trade': stats[8] or 0,
        },
        'per_symbol': per_symbol
    }

def calculate_win_rate(tp, total):
    """Рассчитать win rate"""
    if total == 0:
        return 0
    return (tp / total) * 100

def format_stats_report(label, stats):
    """Форматировать отчёт"""
    total = stats['total']
    win_rate = calculate_win_rate(total['tp'], total['trades'])
    timeout_rate = (total['timeout'] / total['trades'] * 100) if total['trades'] > 0 else 0
    
    report = f"\n{'='*70}\n"
    report += f"{label}\n"
    report += f"{'='*70}\n\n"
    
    report += "ОБЩАЯ СТАТИСТИКА:\n"
    report += f"   Total Trades:     {total['trades']}\n"
    report += f"   TP (wins):        {total['tp']} ({win_rate:.2f}%)\n"
    report += f"   TIMEOUT (losses): {total['timeout']} ({timeout_rate:.2f}%)\n"
    report += f"   SL (losses):      {total['sl']}\n"
    report += f"\n"
    report += f"   Total P&L:        ${total['total_pnl']:.2f}\n"
    report += f"   Avg P&L/Trade:    ${total['avg_pnl']:.4f}\n"
    report += f"   Avg Duration:     {total['avg_duration']:.1f}s\n"
    report += f"   Best Trade:       ${total['best_trade']:.4f}\n"
    report += f"   Worst Trade:      ${total['worst_trade']:.4f}\n"
    report += f"\n"
    
    report += "ПО СИМВОЛАМ:\n"
    if len(stats['per_symbol']) == 0:
        report += "   (нет данных)\n"
    else:
        for row in stats['per_symbol']:
            symbol, trades, tp, timeout, total_pnl, avg_pnl, avg_dur = row
            symbol_wr = calculate_win_rate(tp, trades)
            symbol_tor = (timeout / trades * 100) if trades > 0 else 0
            
            report += f"\n   {symbol}:\n"
            report += f"      Trades: {trades} | WR: {symbol_wr:.1f}% | Timeout: {symbol_tor:.1f}%\n"
            report += f"      P&L: ${total_pnl:.2f} | Avg: ${avg_pnl:.4f} | Dur: {avg_dur:.1f}s\n"
    
    return report

# ═══════════════════════════════════════════════════════════════════════
# ОСНОВНОЙ АНАЛИЗ
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*70)
    print("АНАЛИЗ РЕЗУЛЬТАТОВ ML ТЕСТА")
    print("="*70 + "\n")
    
    output = ""
    
    # ML Phase
    if Path(ML_DB).exists():
        print("[OK] Анализ ML Phase...")
        ml_stats = get_trades_stats(ML_DB)
        ml_report = format_stats_report("[ML PHASE] С ML ФИЛЬТРАЦИЕЙ", ml_stats)
        output += ml_report
        print(ml_report)
    else:
        print(f"[ERROR] Файл {ML_DB} не найден!")
        return
    
    # Baseline
    if Path(BASELINE_DB).exists():
        print("\n[OK] Анализ Baseline...")
        baseline_stats = get_trades_stats(BASELINE_DB)
        baseline_report = format_stats_report("[BASELINE] БЕЗ ML", baseline_stats)
        output += baseline_report
        print(baseline_report)
        
        # Сравнение
        ml_total = ml_stats['total']
        bl_total = baseline_stats['total']
        
        ml_wr = calculate_win_rate(ml_total['tp'], ml_total['trades'])
        bl_wr = calculate_win_rate(bl_total['tp'], bl_total['trades'])
        
        ml_tor = (ml_total['timeout'] / ml_total['trades'] * 100) if ml_total['trades'] > 0 else 0
        bl_tor = (bl_total['timeout'] / bl_total['trades'] * 100) if bl_total['trades'] > 0 else 0
        
        comparison = f"\n{'='*70}\n"
        comparison += "СРАВНЕНИЕ ML vs BASELINE\n"
        comparison += f"{'='*70}\n\n"
        
        comparison += "Win Rate:\n"
        comparison += f"   ML:       {ml_wr:.2f}%\n"
        comparison += f"   Baseline: {bl_wr:.2f}%\n"
        delta_wr = ml_wr - bl_wr
        comparison += f"   Разница:  {delta_wr:+.2f}% {'[+]' if delta_wr > 0 else '[-]'}\n\n"
        
        comparison += "Timeout Rate:\n"
        comparison += f"   ML:       {ml_tor:.2f}%\n"
        comparison += f"   Baseline: {bl_tor:.2f}%\n"
        delta_tor = ml_tor - bl_tor
        comparison += f"   Разница:  {delta_tor:+.2f}% {'[+]' if delta_tor < 0 else '[-]'}\n\n"
        
        comparison += "Avg P&L per Trade:\n"
        comparison += f"   ML:       ${ml_total['avg_pnl']:.4f}\n"
        comparison += f"   Baseline: ${bl_total['avg_pnl']:.4f}\n"
        delta_pnl = ml_total['avg_pnl'] - bl_total['avg_pnl']
        comparison += f"   Разница:  ${delta_pnl:+.4f} {'[+]' if delta_pnl > 0 else '[-]'}\n\n"
        
        comparison += "Total P&L:\n"
        comparison += f"   ML:       ${ml_total['total_pnl']:.2f}\n"
        comparison += f"   Baseline: ${bl_total['total_pnl']:.2f}\n"
        delta_total = ml_total['total_pnl'] - bl_total['total_pnl']
        comparison += f"   Разница:  ${delta_total:+.2f} {'[+]' if delta_total > 0 else '[-]'}\n\n"
        
        comparison += "Avg Duration:\n"
        comparison += f"   ML:       {ml_total['avg_duration']:.1f}s\n"
        comparison += f"   Baseline: {bl_total['avg_duration']:.1f}s\n"
        comparison += f"   Разница:  {ml_total['avg_duration'] - bl_total['avg_duration']:+.1f}s\n\n"
        
        # Вывод
        output += comparison
        print(comparison)
        
        # Вердикт
        verdict = f"\n{'='*70}\n"
        verdict += "ВЕРДИКТ\n"
        verdict += f"{'='*70}\n\n"
        
        improvements = 0
        if ml_wr >= bl_wr:
            improvements += 1
        if ml_tor <= bl_tor:
            improvements += 1
        if ml_total['avg_pnl'] >= bl_total['avg_pnl']:
            improvements += 1
        
        if improvements >= 2:
            verdict += "[SUCCESS] ML МОДЕЛЬ УЛУЧШИЛА РЕЗУЛЬТАТЫ!\n\n"
            verdict += "Рекомендация: Продолжить с ML в production\n"
        else:
            verdict += "[WARNING] ML модель не показала значительных улучшений\n\n"
            verdict += "Рекомендация: Переобучить модель или использовать rule-based\n"
        
        output += verdict
        print(verdict)
    
    else:
        print(f"[WARNING] Файл {BASELINE_DB} не найден. Пропуск сравнения.")
    
    # Сохранить отчёт
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(output)
    
    print(f"\n[OK] Отчёт сохранён: {OUTPUT_FILE}\n")

# ═══════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[ERROR] Ошибка выполнения: {e}")
        import traceback
        traceback.print_exc()