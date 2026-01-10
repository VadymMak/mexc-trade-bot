"""
ML Predictor Service - XGBoost JSON model
Predicts TP probability for trading entries

ВАЖНО: predict() теперь async и использует thread pool
для предотвращения блокировки event loop!
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Optional
import pandas as pd

logger = logging.getLogger(__name__)

# Try to import xgboost
try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    logger.warning("xgboost not installed, ML predictor disabled")


class MLPredictor:
    """
    XGBoost model predictor for trade entry filtering.
    
    Usage:
        predictor = MLPredictor(model_path="ml_models/mexc_ml_v1.json")
        score = await predictor.predict(features)  # Теперь async!
        
        if score > 0.6:  # Confidence threshold
            # Enter trade
    """
    
    def __init__(
        self,
        model_path: str = "ml_models/mexc_ml_v1.json",
        enabled: bool = True,
        min_confidence: float = 0.6,
        weight: float = 0.2,
    ):
        self.enabled = enabled and XGBOOST_AVAILABLE
        self.min_confidence = min_confidence
        self.weight = weight
        self.model: Optional[xgb.XGBClassifier] = None
        self.model_path = Path(model_path)
        self.model_version = "unknown"
        self.predictions_count = 0
        self.features_used = []
        
        # ═══ RATE LIMITING: Semaphore to limit concurrent predictions ═══
        self._prediction_semaphore = asyncio.Semaphore(1)  # Max 3 concurrent
        self._last_prediction: Dict[str, float] = {}  # symbol -> timestamp
        self._prediction_cooldown = 5.0  # 1 second between predictions per symbol
        
        if self.enabled:
            self._load_model()  # Sync call - OK в __init__
        else:
            if not XGBOOST_AVAILABLE:
                logger.warning("[ML] xgboost not available, predictor disabled")
            else:
                logger.info("[ML] Predictor disabled by config")
    
    def _load_model(self):
        """
        Load XGBoost model from JSON file.
        
        ВАЖНО: Эта функция sync - она вызывается из __init__!
        """
        try:
            if not self.model_path.exists():
                logger.error(f"[ML] Model file not found: {self.model_path}")
                self.enabled = False
                return
            
            # Load XGBoost model
            self.model = xgb.XGBClassifier()
            self.model.load_model(str(self.model_path))
            
            # Load feature info
            info_path = self.model_path.parent / "model_info.json"
            if info_path.exists():
                with open(info_path, 'r') as f:
                    info = json.load(f)
                    self.features_used = info.get('feature_names', [])
                    self.model_version = f"v1_{info.get('training_samples', 0)}"
            else:
                # Default features from training
                self.features_used = [
                    'spread_bps_entry',
                    'imbalance_entry',
                    'sym_ALGOUSDT',
                    'sym_LINKUSDT',
                    'sym_SOLUSDT',
                    'sym_TRXUSDT',
                    'sym_VETUSDT',
                    'sym_XRPUSDT',
                ]
                self.model_version = "v1_default"
            
            logger.info(
                f"[ML] ✅ Model loaded: {self.model_version}, "
                f"Features: {len(self.features_used)}"
            )
            logger.info(f"[ML] Feature names: {self.features_used}")
        
        except Exception as e:
            logger.error(f"[ML] Failed to load model: {e}", exc_info=True)
            self.enabled = False
    
    async def predict(self, features: Dict[str, float]) -> float:
        """
        Predict TP probability for given features.
        
        Args:
            features: Dict with keys:
                - symbol: str (e.g. "LINKUSDT")
                - spread_bps_entry: float
                - imbalance_entry: float
        
        Returns:
            Probability of TP (0.0 to 1.0)
            If model disabled or error: 0.5 (neutral)
        """
        if not self.enabled or self.model is None:
            return 0.5  # Neutral score
        
        # ═══ RATE LIMITING: Check cooldown per symbol ═══
        symbol = features.get('symbol', 'UNKNOWN')
        now = asyncio.get_event_loop().time()
        last_pred = self._last_prediction.get(symbol, 0)

        # Add random jitter (0-2 seconds) to prevent all symbols predicting at once
        import random
        jitter = random.uniform(0, 2.0)
        cooldown_with_jitter = self._prediction_cooldown + jitter

        if (now - last_pred) < cooldown_with_jitter:
            # Too soon - return cached neutral score
            return 0.5
        
        # ═══ SEMAPHORE: Limit concurrent predictions ═══
        async with self._prediction_semaphore:
            try:
                # Update timestamp
                self._last_prediction[symbol] = now
                
                # Create feature dict with one-hot encoding for symbol
                feature_dict = {
                    'spread_bps_entry': features.get('spread_bps_entry', 5.0),
                    'imbalance_entry': features.get('imbalance_entry', 0.5),
                    'sym_ALGOUSDT': 1 if symbol == 'ALGOUSDT' else 0,
                    'sym_LINKUSDT': 1 if symbol == 'LINKUSDT' else 0,
                    'sym_SOLUSDT': 1 if symbol == 'SOLUSDT' else 0,
                    'sym_TRXUSDT': 1 if symbol == 'TRXUSDT' else 0,
                    'sym_VETUSDT': 1 if symbol == 'VETUSDT' else 0,
                    'sym_XRPUSDT': 1 if symbol == 'XRPUSDT' else 0,
                }
                
                # Create DataFrame in correct order
                df = pd.DataFrame([feature_dict])
                
                # Ensure columns are in the same order as training
                df = df[self.features_used]
                
                # ═══════════════════════════════════════════════════════════
                # PREDICTION WITH TIMEOUT
                # ═══════════════════════════════════════════════════════════
                loop = asyncio.get_event_loop()
                
                try:
                    # Run XGBoost in thread pool with timeout
                    proba = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,  # default ThreadPoolExecutor
                            self.model.predict_proba,
                            df
                        ),
                        timeout=0.5  # 500ms timeout
                    )
                    
                    # proba shape: (1, 2) - [prob_class_0, prob_class_1]
                    score = float(proba[0][1])  # Probability of class 1 (TP)
                
                except asyncio.TimeoutError:
                    logger.warning(f"[ML] ⏱️ Prediction timeout for {symbol}")
                    return 0.5  # Neutral score on timeout (fail open)
                
                self.predictions_count += 1
                
                # Log every 100 predictions
                if self.predictions_count % 100 == 0:
                    logger.info(
                        f"[ML] Predictions: {self.predictions_count}, "
                        f"Last score: {score:.3f}"
                    )
                
                return score
            
            except Exception as e:
                logger.error(f"[ML] Prediction failed: {e}", exc_info=True)
                return 0.5  # Fallback to neutral
    
    async def should_enter_trade(
        self,
        features: Dict[str, float],
        rule_score: float,
        use_filter: bool = True,
        use_weight: bool = False,
    ) -> tuple[bool, float]:
        """
        Decide whether to enter trade.
        
        ВАЖНО: Теперь async и использует await для predict()!
        
        Args:
            features: Market features (must include 'symbol')
            rule_score: Score from rule-based strategy (0-100)
            use_filter: Use ML as filter (block low scores)
            use_weight: Use ML as weight (combine with rule_score)
        
        Returns:
            (should_enter, final_score)
        """
        if not self.enabled:
            return True, rule_score  # ML disabled → rely on rules
        
        # ═══════════════════════════════════════════════════════════
        # ВАЖНО: await здесь! predict() теперь async!
        # ═══════════════════════════════════════════════════════════
        ml_score = await self.predict(features)
        
        # MODE 1: ML AS FILTER
        if use_filter:
            if ml_score < self.min_confidence:
                symbol = features.get('symbol', 'UNKNOWN')
                logger.info(
                    f"[ML] ❌ Filtered: {symbol} ml_score={ml_score:.3f} < "
                    f"{self.min_confidence}"
                )
                return False, 0.0
            else:
                symbol = features.get('symbol', 'UNKNOWN')
                logger.info(
                    f"[ML] ✅ Passed: {symbol} ml_score={ml_score:.3f} >= "
                    f"{self.min_confidence}"
                )
                return True, rule_score  # Passed filter → use rule_score
        
        # MODE 2: ML AS WEIGHT
        if use_weight:
            # Combined score
            # rule_score normalized 0-100, ml_score 0-1
            normalized_rule = rule_score / 100.0
            final_score = (
                normalized_rule * (1 - self.weight) + ml_score * self.weight
            ) * 100
            
            logger.debug(
                f"[ML] Combined: rule={rule_score:.1f}, ml={ml_score:.3f}, "
                f"final={final_score:.1f}"
            )
            
            return True, final_score
        
        # Default: only rule_score
        return True, rule_score
    
    def get_stats(self) -> Dict:
        """Get ML predictor statistics"""
        return {
            "enabled": self.enabled,
            "xgboost_available": XGBOOST_AVAILABLE,
            "model_version": self.model_version,
            "model_path": str(self.model_path),
            "predictions_count": self.predictions_count,
            "min_confidence": self.min_confidence,
            "weight": self.weight,
            "features_count": len(self.features_used),
            "status": "loaded" if self.model is not None else "not_loaded",
        }


# ═══════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════

_predictor: Optional[MLPredictor] = None


def get_ml_predictor() -> MLPredictor:
    """
    Get singleton instance of ML predictor.
    
    ВАЖНО: Эта функция sync! Она просто возвращает singleton.
    """
    global _predictor
    if _predictor is None:
        # Load from settings/env
        try:
            from app.config.settings import settings
            
            _predictor = MLPredictor(
                model_path=getattr(settings, "ML_MODEL_PATH", "ml_models/mexc_ml_v1.json"),
                enabled=getattr(settings, "ML_ENABLED", False),
                min_confidence=getattr(settings, "ML_MIN_CONFIDENCE", 0.6),
                weight=getattr(settings, "ML_WEIGHT", 0.2),
            )
        except Exception as e:
            logger.warning(f"[ML] Could not load from settings: {e}")
            _predictor = MLPredictor(enabled=False)
    
    return _predictor
