"""
ML Model Training - V4
Train XGBoost to predict TP vs TIMEOUT
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json
import pickle
from datetime import datetime

from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, classification_report, confusion_matrix
)
from xgboost import XGBClassifier
import matplotlib.pyplot as plt

print("=" * 70)
print("ML MODEL TRAINING - V4")
print("=" * 70)

# Load data
data_path = Path('ml_data/training_data_v4.csv')
if not data_path.exists():
    print(f"❌ Data not found: {data_path}")
    print("   Run: python scripts/ml_export_with_labels.py")
    exit(1)

df = pd.read_csv(data_path)
print(f"✅ Loaded {len(df)} trades")

# Check label distribution
print(f"\n📊 LABEL DISTRIBUTION:")
print(df['label'].value_counts())
print(f"Positive rate: {df['label'].mean():.2%}")

# Feature engineering
print(f"\n🔧 FEATURE ENGINEERING...")

# Parse strategy_params JSON if exists
if 'strategy_params' in df.columns:
    try:
        params = df['strategy_params'].apply(lambda x: json.loads(x) if pd.notna(x) else {})
        # Extract useful params if needed
    except:
        pass

# Feature set
feature_cols = [
    'spread_bps_entry',
    'imbalance_entry',
    'depth_5bps_entry',
    'pnl_bps',  # Can use for feature but not for prediction
]

# Add symbol as categorical
df['symbol_cat'] = pd.Categorical(df['symbol']).codes

feature_cols.append('symbol_cat')

# Drop rows with missing features
df_clean = df[feature_cols + ['label']].dropna()
print(f"✅ Clean data: {len(df_clean)} rows")

# Separate features and target
X = df_clean[feature_cols]
y = df_clean['label']

# Train/test split
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

print(f"\n📊 TRAIN/TEST SPLIT:")
print(f"Train: {len(X_train)} samples")
print(f"Test:  {len(X_test)} samples")

# Train model
print(f"\n🤖 TRAINING MODEL...")

model = XGBClassifier(
    n_estimators=100,
    max_depth=5,
    learning_rate=0.1,
    random_state=42,
    eval_metric='logloss',
    use_label_encoder=False,
)

model.fit(
    X_train, y_train,
    eval_set=[(X_test, y_test)],
    verbose=False
)

print("✅ Model trained!")

# Evaluate
print(f"\n📊 MODEL EVALUATION:")

# Predictions
y_pred = model.predict(X_test)
y_pred_proba = model.predict_proba(X_test)[:, 1]

# Metrics
accuracy = accuracy_score(y_test, y_pred)
precision = precision_score(y_test, y_pred)
recall = recall_score(y_test, y_pred)
f1 = f1_score(y_test, y_pred)
auc = roc_auc_score(y_test, y_pred_proba)

print(f"Accuracy:  {accuracy:.3f}")
print(f"Precision: {precision:.3f} (когда предсказываем TP, как часто правы)")
print(f"Recall:    {recall:.3f} (сколько TP мы нашли)")
print(f"F1-Score:  {f1:.3f}")
print(f"AUC-ROC:   {auc:.3f}")

# Confusion matrix
print(f"\n📊 CONFUSION MATRIX:")
cm = confusion_matrix(y_test, y_pred)
print(f"              Predicted")
print(f"              0 (TO)  1 (TP)")
print(f"Actual 0 (TO)  {cm[0,0]:4d}   {cm[0,1]:4d}")
print(f"Actual 1 (TP)  {cm[1,0]:4d}   {cm[1,1]:4d}")

# Feature importance
print(f"\n📊 FEATURE IMPORTANCE:")
importance = model.feature_importances_
for feat, imp in sorted(zip(feature_cols, importance), key=lambda x: x[1], reverse=True):
    print(f"  {feat:<20} {imp:.4f}")

# Cross-validation
print(f"\n📊 CROSS-VALIDATION (5-fold):")
cv_scores = cross_val_score(model, X_train, y_train, cv=5, scoring='f1')
print(f"CV F1-scores: {cv_scores}")
print(f"Mean: {cv_scores.mean():.3f} (+/- {cv_scores.std() * 2:.3f})")

# Quality assessment
print(f"\n📊 MODEL QUALITY ASSESSMENT:")

if accuracy > 0.80 and precision > 0.85 and recall > 0.70:
    print("✅ EXCELLENT MODEL!")
    print("   - High accuracy")
    print("   - High precision (few false positives)")
    print("   - Good recall (catches most TP)")
elif accuracy > 0.70 and precision > 0.75:
    print("🟡 GOOD MODEL")
    print("   - Acceptable for production")
    print("   - May need tuning")
else:
    print("❌ POOR MODEL")
    print("   - Needs more data or better features")
    print("   - Consider collecting more negative examples")

# Save model
output_dir = Path('ml_models')
output_dir.mkdir(exist_ok=True)

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
model_file = output_dir / f'xgb_v4_{timestamp}.pkl'

model_data = {
    'model': model,
    'version': f'v4_{timestamp}',
    'features': feature_cols,
    'metrics': {
        'accuracy': accuracy,
        'precision': precision,
        'recall': recall,
        'f1': f1,
        'auc': auc,
    },
    'trained_at': datetime.now().isoformat(),
    'train_size': len(X_train),
    'test_size': len(X_test),
}

with open(model_file, 'wb') as f:
    pickle.dump(model_data, f)

print(f"\n✅ Model saved: {model_file}")

# Create symlink to latest
latest_link = output_dir / 'xgb_latest.pkl'
if latest_link.exists():
    latest_link.unlink()
latest_link.symlink_to(model_file.name)

print(f"✅ Symlink created: {latest_link}")

# Example predictions
print(f"\n📊 EXAMPLE PREDICTIONS:")

# Best examples (high TP probability)
top_indices = y_pred_proba.argsort()[-5:][::-1]
print("\nTop 5 predicted TPs:")
for idx in top_indices:
    actual = y_test.iloc[idx]
    pred_proba = y_pred_proba[idx]
    print(f"  Actual: {actual}, Predicted: {pred_proba:.3f}")

# Worst examples (low TP probability)
bottom_indices = y_pred_proba.argsort()[:5]
print("\nTop 5 predicted TIMEOUTs:")
for idx in bottom_indices:
    actual = y_test.iloc[idx]
    pred_proba = y_pred_proba[idx]
    print(f"  Actual: {actual}, Predicted: {pred_proba:.3f}")

print("\n" + "=" * 70)
print("✅ Training complete!")
print("=" * 70)
print("\nNext steps:")
print("  1. Review metrics above")
print("  2. If model is good → integrate into backend")
print("  3. If model is poor → collect more data or tune hyperparameters")
print(f"\n  Model file: {model_file}")
print(f"  Use in code: get_ml_predictor(model_path='{model_file}')")