"""
Анализ сделок из ml_trade_outcomes
"""
import sqlite3
import pandas as pd
from datetime import datetime

DB_PATH = "../backend/mexc.db"

def analyze_trades():
    conn = sqlite3.connect(DB_PATH)
    
    # Загрузить все сделки
    df = pd.read_sql_query("""
        SELECT 
            trade_id, symbol, entry_time, exit_time, exit_reason,
            pnl_bps, pnl_usd, hold_duration_sec,
            max_favorable_excursion_bps as mfe_bps,
            max_adverse_excursion_bps as mae_bps,
            optimal_tp_bps, optimal_sl_bps,
            win, hit_tp, hit_sl, hit_trailing, timed_out,
            spread_bps_entry, eff_spread_bps_entry,
            depth5_bid_usd_entry, depth5_ask_usd_entry,
            imbalance_entry, atr1m_pct_entry,
            hour_of_day, day_of_week
        FROM ml_trade_outcomes
        ORDER BY entry_time DESC
    """, conn)
    
    conn.close()
    
    if len(df) == 0:
        print("❌ Нет данных для анализа!")
        return
    
    print("=" * 70)
    print(f"📊 АНАЛИЗ ML TRADE OUTCOMES: {len(df)} сделок")
    print("=" * 70)
    
    # Общая статистика
    print(f"\n📈 ОБЩАЯ СТАТИСТИКА:")
    print(f"   Всего сделок:     {len(df)}")
    print(f"   Прибыльных:       {df['win'].sum()} ({df['win'].mean()*100:.1f}%)")
    print(f"   Убыточных:        {(1-df['win']).sum()} ({(1-df['win'].mean())*100:.1f}%)")
    print(f"   Средний PnL:      {df['pnl_bps'].mean():.2f} bps")
    print(f"   Медианный PnL:    {df['pnl_bps'].median():.2f} bps")
    print(f"   Total PnL:        ${df['pnl_usd'].sum():.2f}")
    
    # По символам
    print(f"\n📋 ПО СИМВОЛАМ:")
    symbol_stats = df.groupby('symbol').agg({
        'trade_id': 'count',
        'win': 'mean',
        'pnl_bps': 'mean',
        'pnl_usd': 'sum',
        'mfe_bps': 'mean',
        'mae_bps': 'mean',
        'hold_duration_sec': 'mean'
    }).round(2)
    symbol_stats.columns = ['Trades', 'WinRate', 'AvgPnL_bps', 'TotalPnL_USD', 'AvgMFE', 'AvgMAE', 'AvgDuration']
    print(symbol_stats.to_string())
    
    # MFE/MAE анализ
    print(f"\n🎯 MFE/MAE АНАЛИЗ:")
    print(f"   Средний MFE:      {df['mfe_bps'].mean():.2f} bps")
    print(f"   Средний MAE:      {df['mae_bps'].mean():.2f} bps")
    print(f"   MFE/MAE Ratio:    {abs(df['mfe_bps'].mean() / df['mae_bps'].mean()) if df['mae_bps'].mean() != 0 else 'N/A':.2f}")
    
    # Оптимальные параметры
    print(f"\n⚙️ ОПТИМАЛЬНЫЕ ПАРАМЕТРЫ:")
    print(f"   Optimal TP:       {df['optimal_tp_bps'].mean():.2f} bps")
    print(f"   Optimal SL:       {df['optimal_sl_bps'].mean():.2f} bps")
    
    # Exit reasons
    print(f"\n🚪 ПРИЧИНЫ ВЫХОДА:")
    exit_counts = df['exit_reason'].value_counts()
    for reason, count in exit_counts.items():
        print(f"   {reason:12s}: {count:3d} ({count/len(df)*100:.1f}%)")
    
    # По времени суток
    print(f"\n⏰ ПО ВРЕМЕНИ СУТОК:")
    hour_stats = df.groupby('hour_of_day').agg({
        'trade_id': 'count',
        'win': 'mean',
        'pnl_bps': 'mean'
    }).round(2)
    hour_stats.columns = ['Trades', 'WinRate', 'AvgPnL']
    print(hour_stats.head(10).to_string())
    
    # Рекомендации
    print(f"\n💡 РЕКОМЕНДАЦИИ:")
    
    best_symbol = symbol_stats['WinRate'].idxmax()
    worst_symbol = symbol_stats['WinRate'].idxmin()
    
    print(f"   ✅ Лучший символ:     {best_symbol} (WinRate: {symbol_stats.loc[best_symbol, 'WinRate']*100:.1f}%)")
    print(f"   ❌ Худший символ:     {worst_symbol} (WinRate: {symbol_stats.loc[worst_symbol, 'WinRate']*100:.1f}%)")
    
    avg_mfe = df['mfe_bps'].mean()
    avg_mae = df['mae_bps'].mean()
    
    print(f"   🎯 Рекомендуемый TP:  {avg_mfe * 0.8:.1f} bps (80% от MFE)")
    print(f"   🛡️ Рекомендуемый SL:  {avg_mae * 1.5:.1f} bps (150% от MAE)")
    
    if len(df) < 50:
        print(f"\n⚠️  Мало данных! Соберите минимум 50 сделок для надёжного анализа.")
    
    print("\n" + "=" * 70)

if __name__ == "__main__":
    analyze_trades()