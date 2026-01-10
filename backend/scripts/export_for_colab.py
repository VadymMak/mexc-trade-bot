# scripts/export_ml_for_colab.py

"""
Export from ml_trade_outcomes table (77 columns)
"""

import sqlite3
import pandas as pd
from datetime import datetime

def export_ml_for_colab():
    print("🔄 Exporting ML dataset for Colab...\n")
    
    conn = sqlite3.connect('mexc.db')
    
    # Export from ML table (not trades table!)
    query = "SELECT * FROM ml_trade_outcomes WHERE exit_time IS NOT NULL"
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    print(f"✅ Loaded: {len(df)} trades")
    print(f"✅ Columns: {len(df.columns)}")
    
    # Save
    filename = f'ml_dataset_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    df.to_csv(filename, index=False)
    
    print(f"\n✅ Saved: {filename}")
    print(f"📊 Size: {len(df)} rows × {len(df.columns)} columns")
    
    # Stats
    print("\n" + "="*60)
    print("DATASET STATS")
    print("="*60)
    
    print(f"✅ Win Rate: {df['win'].mean():.1%}")
    print(f"✅ Exploration Rate: {df['exploration_mode'].mean():.1%}")
    print(f"✅ Symbols: {', '.join(df['symbol'].unique())}")
    
    print(f"\n📊 Exit Reasons:")
    print(df['exit_reason'].value_counts())
    
    print(f"\n🎯 Key Columns Check:")
    key_cols = ['win', 'hit_tp', 'hit_sl', 'spread_bps_entry', 'imbalance_entry']
    for col in key_cols:
        if col in df.columns:
            print(f"   ✅ {col}")
        else:
            print(f"   ❌ {col}")
    
    return filename

if __name__ == '__main__':
    export_ml_for_colab()