import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

DB = Path('mexc.db')
OUTPUT = Path('ml_data')
OUTPUT.mkdir(exist_ok=True)

db = sqlite3.connect(str(DB))

print("=" * 60)
print("EXPORTING FILLS FOR ML (v3 - No Leakage)")
print("=" * 60)

query = """
SELECT 
    datetime(executed_at) as timestamp,
    symbol,
    side,
    price,
    qty,
    quote_qty
FROM fills
ORDER BY executed_at
"""

df = pd.read_sql_query(query, db)
db.close()

print(f"Loaded: {len(df):,} fills")

# Временные фичи
df['timestamp'] = pd.to_datetime(df['timestamp'])
df['hour'] = df['timestamp'].dt.hour
df['day_of_week'] = df['timestamp'].dt.dayofweek
df['minute_of_day'] = df['hour'] * 60 + df['timestamp'].dt.minute

# Окна
df['time_bucket'] = df['timestamp'].dt.floor('5min')

# Агрегация БЕЗ quote_qty (чтобы не было утечки)
features = df.groupby(['symbol', 'time_bucket']).agg({
    'price': ['mean', 'std', 'min', 'max'],
    'qty': ['sum', 'mean'],
    'hour': 'first',
    'day_of_week': 'first',
    'minute_of_day': 'first'
}).reset_index()

features.columns = ['_'.join(col).strip('_') for col in features.columns]

# Target: средняя прибыль в СЛЕДУЮЩЕМ окне
# Джойним quote_qty отдельно для создания таргета
quote_by_window = df.groupby(['symbol', 'time_bucket'])['quote_qty'].sum().reset_index()
quote_by_window.columns = ['symbol', 'time_bucket', 'future_profit']

# Сдвиг на 1 окно вперед (следующие 5 минут)
quote_by_window['time_bucket'] = quote_by_window.groupby('symbol')['time_bucket'].shift(-1)

# Merge
features = features.merge(quote_by_window, on=['symbol', 'time_bucket'], how='left')

# Убираем последние окна (где нет future)
features = features.dropna(subset=['future_profit'])

# Target: прибыль в следующем окне выше медианы?
median_profit = features['future_profit'].median()
features['target'] = (features['future_profit'] > median_profit).astype(int)

# Удаляем future_profit из фичей (чтобы не было утечки)
features = features.drop(['future_profit'], axis=1)

print(f"\nAggregated: {len(features):,} windows")
print(f"Median future profit: ${median_profit:.2f}")
print(f"\nTarget distribution:")
print(f"  Good: {features['target'].sum()} ({features['target'].mean()*100:.1f}%)")
print(f"  Bad:  {(features['target']==0).sum()} ({(features['target']==0).mean()*100:.1f}%)")

output = OUTPUT / 'features_fills_v3.csv'
features.to_csv(output, index=False)

print(f"\n✅ Saved: {output}")
print(f"   Shape: {features.shape}")
print("=" * 60)