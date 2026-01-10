import pandas as pd
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score
import xgboost as xgb
import pickle
from datetime import datetime

DATA = Path('ml_data/features_fills_v3.csv')
MODELS = Path('ml_models')
MODELS.mkdir(exist_ok=True)

print("=" * 60)
print("TRAINING ML MODEL (v3 - No Leakage)")
print("=" * 60)

df = pd.read_csv(DATA)
print(f"Samples: {len(df):,}")

# Features (БЕЗ quote_qty!)
feature_cols = [c for c in df.columns if c not in ['symbol', 'time_bucket', 'target']]
X = df[feature_cols]
y = df['target']

print(f"Features: {len(feature_cols)}")
print(f"  {feature_cols}")
print(f"\nTarget: {y.value_counts().to_dict()}")

# Split
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

print(f"\nTrain: {len(X_train):,}, Test: {len(X_test):,}")

# Train
print("\n[Training XGBoost...]")
model = xgb.XGBClassifier(
    n_estimators=100,
    max_depth=4,  # Уменьшим depth чтобы не переобучаться
    learning_rate=0.05,  # Меньше LR
    min_child_weight=5,  # Больше регуляризация
    random_state=42
)
model.fit(X_train, y_train)

# Evaluate
y_pred = model.predict(X_test)
y_proba = model.predict_proba(X_test)[:, 1]

print("\n" + "=" * 60)
print("MODEL PERFORMANCE")
print("=" * 60)
print(classification_report(y_test, y_pred, target_names=['Bad', 'Good']))
print(f"\nROC AUC: {roc_auc_score(y_test, y_proba):.4f}")

# Feature importance
imp = pd.DataFrame({
    'feature': feature_cols,
    'importance': model.feature_importances_
}).sort_values('importance', ascending=False)

print("\n" + "=" * 60)
print("FEATURE IMPORTANCE")
print("=" * 60)
print(imp.to_string(index=False))

# Save
timestamp = datetime.now().strftime("%Y%m%d_%H%M")
model_file = MODELS / f'xgb_v3_{timestamp}.pkl'

with open(model_file, 'wb') as f:
    pickle.dump({
        'model': model,
        'features': feature_cols,
        'roc_auc': roc_auc_score(y_test, y_proba),
        'timestamp': timestamp
    }, f)

# Latest
latest = MODELS / 'xgb_latest.pkl'
if latest.exists():
    latest.unlink()

import shutil
shutil.copy(model_file, latest)

print(f"\n✅ Saved: {model_file.name}")
print(f"✅ Latest: xgb_latest.pkl")
print("=" * 60)