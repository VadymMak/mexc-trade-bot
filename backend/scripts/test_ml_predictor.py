"""
Test ML Predictor
Quick test to verify ML model loads and predicts correctly
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.ml_predictor import MLPredictor

print("=" * 70)
print("ML PREDICTOR TEST")
print("=" * 70)

# Initialize predictor
print("\n📦 Initializing ML Predictor...")
predictor = MLPredictor(
    model_path="ml_models/mexc_ml_v1.json",
    enabled=True,
    min_confidence=0.6,
)

# Check stats
stats = predictor.get_stats()
print(f"\n📊 ML Stats:")
for key, value in stats.items():
    print(f"   {key}: {value}")

print("\n" + "=" * 70)
print("TESTING PREDICTIONS")
print("=" * 70)

# Test 1: Good symbol (LINKUSDT)
print("\n✅ TEST 1: LINKUSDT (Good symbol)")
features_good = {
    "symbol": "LINKUSDT",
    "spread_bps_entry": 5.7,
    "imbalance_entry": 0.45,
}
score_good = predictor.predict(features_good)
print(f"   Features: {features_good}")
print(f"   ML Score: {score_good:.3f}")
print(f"   Expected: >0.8 (high probability of TP)")

# Test 2: Bad symbol (TRXUSDT)
print("\n❌ TEST 2: TRXUSDT (Bad symbol)")
features_bad = {
    "symbol": "TRXUSDT",
    "spread_bps_entry": 3.2,
    "imbalance_entry": 0.62,
}
score_bad = predictor.predict(features_bad)
print(f"   Features: {features_bad}")
print(f"   ML Score: {score_bad:.3f}")
print(f"   Expected: <0.3 (low probability of TP)")

# Test 3: Medium symbol (XRPUSDT)
print("\n🟡 TEST 3: XRPUSDT (Medium symbol)")
features_medium = {
    "symbol": "XRPUSDT",
    "spread_bps_entry": 4.5,
    "imbalance_entry": 0.51,
}
score_medium = predictor.predict(features_medium)
print(f"   Features: {features_medium}")
print(f"   ML Score: {score_medium:.3f}")
print(f"   Expected: 0.5-0.7 (medium probability)")

# Test 4: Filter test
print("\n" + "=" * 70)
print("FILTER TEST (threshold=0.6)")
print("=" * 70)

for symbol, features in [
    ("LINKUSDT", features_good),
    ("TRXUSDT", features_bad),
    ("XRPUSDT", features_medium),
]:
    should_enter, final_score = predictor.should_enter_trade(
        features=features,
        rule_score=75.0,
        use_filter=True,
    )
    
    result = "✅ PASS" if should_enter else "❌ FILTER"
    print(f"\n{result} {symbol}:")
    print(f"   ML Score: {predictor.predict(features):.3f}")
    print(f"   Decision: {'Enter trade' if should_enter else 'Skip trade'}")

print("\n" + "=" * 70)
print("✅ TEST COMPLETE!")
print("=" * 70)

print("\n💡 EXPECTED RESULTS:")
print("   ✅ LINKUSDT should PASS (score >0.8)")
print("   ❌ TRXUSDT should be FILTERED (score <0.3)")
print("   🟡 XRPUSDT depends on threshold (0.5-0.7)")

print("\n📋 NEXT STEPS:")
print("   1. If tests pass → Add ML to /api/healthz")
print("   2. If tests fail → Check model file")