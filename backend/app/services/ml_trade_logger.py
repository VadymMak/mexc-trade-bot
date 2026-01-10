"""
ML Trade Logger - Dataset v2 Collection
========================================

Logs FULL market features (50+) for every trade:
- Entry snapshot from ScanRow (all scanner features)
- Exit outcome (TP/SL/TIMEOUT/TRAIL)
- Performance metrics (MFE/MAE/duration)

Usage:
    # At trade entry
    logger.log_entry(symbol, scan_row, strategy_params)
    
    # At trade exit
    logger.log_exit(symbol, exit_price, exit_reason, pnl_data)
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dataclasses import asdict

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Phase 2: Enhanced tracking
try:
    from app.services.book_tracker_enhanced import get_enhanced_book_tracker
    BOOK_ENHANCED_AVAILABLE = True
except ImportError:
    BOOK_ENHANCED_AVAILABLE = False

try:
    from app.services.mm_detector import get_mm_detector
    MM_DETECTOR_AVAILABLE = True
except ImportError:
    MM_DETECTOR_AVAILABLE = False

class MLTradeLogger:
    """
    Logs complete trade data with 50+ market features for ML training.
    
    Features logged:
    - Base (3): spread, imbalance, depth
    - Depth details (8): bid/ask at 5bps/10bps
    - Volume (3): usdpm, tpm, median_trade
    - Volatility (6): atr, grinder, pullback, spikes
    - Pattern scores (4): vol_pattern, dca, atr_proxy, grade
    - Fees (3): maker, taker, zero_fee
    - Time (3): hour, day, minute
    - Symbol (1): symbol name
    - Strategy params (6): TP, SL, trailing, timeout
    - Exit metrics (10): pnl, duration, MFE, MAE, reason
    
    Total: 50+ features!
    """
    
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._active_trades: Dict[str, Dict[str, Any]] = {}
        self._trades_logged = 0
        
        if not self.enabled:
            logger.warning("MLTradeLogger is DISABLED")
        else:
            logger.info("✅ MLTradeLogger initialized and enabled")
    
    def log_entry(
        self,
        symbol: str,
        scan_row: Any,  # ScanRow from market_scanner
        strategy_params: Dict[str, Any],
        entry_price: float,
        entry_qty: float,
        trade_id: str,
    ) -> None:
        """
        Log trade entry with full market snapshot.
        
        Args:
            symbol: Trading pair (e.g. "LINKUSDT")
            scan_row: ScanRow object with all market features
            strategy_params: Dict with TP, SL, trailing, timeout
            entry_price: Actual entry price
            entry_qty: Position size
            trade_id: Unique trade identifier
        """
        if not self.enabled:
            return
        
        try:
            # Extract all features from ScanRow
            entry_snapshot = self._extract_features(scan_row)
            
            # Add trade metadata
            entry_snapshot.update({
                'trade_id': trade_id,
                'symbol': symbol,
                'entry_time': datetime.now(timezone.utc),
                'entry_price': entry_price,
                'entry_qty': entry_qty,
                'entry_side': 'BUY',  # Long-only
                
                # Strategy parameters
                'take_profit_bps': strategy_params.get('take_profit_bps', 0.0),
                'stop_loss_bps': strategy_params.get('stop_loss_bps', 0.0),
                'trailing_stop_enabled': int(strategy_params.get('trailing_stop_enabled', False)),
                'trail_activation_bps': strategy_params.get('trail_activation_bps'),
                'trail_distance_bps': strategy_params.get('trail_distance_bps'),
                'timeout_seconds': strategy_params.get('timeout_seconds'),
                'exploration_mode': strategy_params.get('exploration_mode', 0),
                
                # Time context
                'hour_of_day': datetime.now().hour,
                'day_of_week': datetime.now().weekday(),  # 0=Monday
                'minute_of_hour': datetime.now().minute,
            })
            
            # Store in memory until exit
            self._active_trades[symbol] = entry_snapshot
            
            logger.debug(f"📊 Entry logged: {symbol} @ {entry_price:.4f}")
            
        except Exception as e:
            logger.error(f"Failed to log entry for {symbol}: {e}", exc_info=True)
    
    def log_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_qty: float,
        exit_reason: str,
        pnl_usd: float,
        pnl_bps: float,
        pnl_percent: float,
        hold_duration_sec: float,
        max_favorable_excursion_bps: Optional[float] = None,
        max_adverse_excursion_bps: Optional[float] = None,
        peak_price: Optional[float] = None,
        lowest_price: Optional[float] = None,
    ) -> None:
        """
        Log trade exit and save complete record to database.
        
        Args:
            symbol: Trading pair
            exit_price: Actual exit price
            exit_qty: Position size closed
            exit_reason: 'TP', 'SL', 'TRAIL', 'TIMEOUT'
            pnl_usd: Profit/loss in USD
            pnl_bps: Profit/loss in basis points
            pnl_percent: Profit/loss in percent
            hold_duration_sec: Trade duration in seconds
            max_favorable_excursion_bps: Peak profit reached
            max_adverse_excursion_bps: Max drawdown
            peak_price: Highest price during trade
            lowest_price: Lowest price during trade
        """
        if not self.enabled:
            return
        
        # Get entry snapshot
        entry_data = self._active_trades.get(symbol)
        if not entry_data:
            logger.warning(f"No entry data found for {symbol} exit")
            return
        
        try:
            # Build complete trade record
            trade_record = {
                **entry_data,  # All entry features
                
                # Exit state
                'exit_time': datetime.now(timezone.utc),
                'exit_price': exit_price,
                'exit_qty': exit_qty,
                'exit_reason': exit_reason.upper(),
                
                # Trade outcome
                'pnl_usd': pnl_usd,
                'pnl_bps': pnl_bps,
                'pnl_percent': pnl_percent,
                'hold_duration_sec': hold_duration_sec,
                
                # Performance metrics
                'max_favorable_excursion_bps': max_favorable_excursion_bps,
                'max_adverse_excursion_bps': max_adverse_excursion_bps,
                'peak_price': peak_price,
                'lowest_price': lowest_price,
                
                # ML labels
                'win': 1 if pnl_usd > 0 else 0,
                'hit_tp': 1 if exit_reason.upper() == 'TP' else 0,
                'hit_sl': 1 if exit_reason.upper() == 'SL' else 0,
                'hit_trailing': 1 if exit_reason.upper() in ('TRAIL', 'TRAILING') else 0,
                'timed_out': 1 if exit_reason.upper() in ('TIMEOUT', 'TO') else 0,
                
                # Metadata
                'workspace_id': 1,
                'exchange': 'mexc',
                'created_at': datetime.now(timezone.utc),
            }
            
            # Save to database
            self._save_to_db(trade_record)
            
            # Remove from active trades
            del self._active_trades[symbol]
            
            self._trades_logged += 1
            
            logger.info(
                f"✅ Trade logged: {symbol} {exit_reason} "
                f"P&L: ${pnl_usd:+.4f} ({pnl_bps:+.2f} bps) "
                f"Duration: {hold_duration_sec:.1f}s "
                f"[Total: {self._trades_logged}]"
            )
            
        except Exception as e:
            logger.error(f"Failed to log exit for {symbol}: {e}", exc_info=True)
    
    def _extract_features(self, scan_row: Any) -> Dict[str, Any]:
        """
        Extract all 50+ features from ScanRow.
        
        Returns dict with all market features.
        """
        # If scan_row is a dataclass, convert to dict
        if hasattr(scan_row, '__dataclass_fields__'):
            row_dict = asdict(scan_row)
        elif isinstance(scan_row, dict):
            row_dict = scan_row
        else:
            # Try to access attributes
            row_dict = {}
            for attr in dir(scan_row):
                if not attr.startswith('_'):
                    try:
                        row_dict[attr] = getattr(scan_row, attr)
                    except:
                        pass
        
        # Extract depth details from depth_at_bps map with fallback to flat fields
        depth_map = row_dict.get('depth_at_bps', {})
        
        # Try nested structure first
        if isinstance(depth_map, dict) and 5 in depth_map:
            depth5 = depth_map.get(5, {})
            depth10 = depth_map.get(10, {})
        else:
            # Fallback to empty dicts (will use flat fields below)
            depth5 = {}
            depth10 = {}
        
        # Get depth values with fallback to flat fields
        depth5_bid = depth5.get('bid_usd', 0.0) or row_dict.get('depth5_bid_usd', 0.0)
        depth5_ask = depth5.get('ask_usd', 0.0) or row_dict.get('depth5_ask_usd', 0.0)
        depth10_bid = depth10.get('bid_usd', 0.0) or row_dict.get('depth10_bid_usd', 0.0)
        depth10_ask = depth10.get('ask_usd', 0.0) or row_dict.get('depth10_ask_usd', 0.0)
        
        features = {
            # ═══════════════════════════════════════════════════════════
            # BASE FEATURES (3)
            # ═══════════════════════════════════════════════════════════
            'spread_bps_entry': row_dict.get('spread_bps', 0.0),
            'spread_pct_entry': row_dict.get('spread_pct', 0.0),
            'spread_abs_entry': row_dict.get('spread_abs', 0.0),
            'imbalance_entry': row_dict.get('imbalance', 0.5),
            
            # ═══════════════════════════════════════════════════════════
            # EFFECTIVE SPREADS (6)
            # ═══════════════════════════════════════════════════════════
            'eff_spread_bps_entry': row_dict.get('eff_spread_bps', 0.0),
            'eff_spread_pct_entry': row_dict.get('eff_spread_pct', 0.0),
            'eff_spread_abs_entry': row_dict.get('eff_spread_abs', 0.0),
            'eff_spread_maker_bps_entry': row_dict.get('eff_spread_bps_maker', 0.0),
            'eff_spread_taker_bps_entry': row_dict.get('eff_spread_bps_taker', 0.0),
            
            # ═══════════════════════════════════════════════════════════
            # DEPTH FEATURES (8)
            # ═══════════════════════════════════════════════════════════
            'depth5_bid_usd_entry': depth5_bid,
            'depth5_ask_usd_entry': depth5_ask,
            'depth10_bid_usd_entry': depth10_bid,
            'depth10_ask_usd_entry': depth10_ask,
            
            # ═══════════════════════════════════════════════════════════
            # VOLUME FEATURES (5)
            # ═══════════════════════════════════════════════════════════
            'base_volume_24h_entry': row_dict.get('base_volume_24h', 0.0),
            'quote_volume_24h_entry': row_dict.get('quote_volume_24h', 0.0),
            'trades_per_min_entry': row_dict.get('trades_per_min', 0.0),
            'usd_per_min_entry': row_dict.get('usd_per_min', 0.0),
            'median_trade_usd_entry': row_dict.get('median_trade_usd', 0.0),
            
            # ═══════════════════════════════════════════════════════════
            # FEE STRUCTURE (3)
            # ═══════════════════════════════════════════════════════════
            'maker_fee_entry': row_dict.get('maker_fee', 0.0),
            'taker_fee_entry': row_dict.get('taker_fee', 0.0),
            'zero_fee_entry': int(row_dict.get('zero_fee', False)),
            
            # ═══════════════════════════════════════════════════════════
            # VOLATILITY FEATURES - from candles_cache (6)
            # ═══════════════════════════════════════════════════════════
            'atr1m_pct_entry': row_dict.get('atr1m_pct', 0.0),
            'spike_count_90m_entry': row_dict.get('spike_count_90m', 0),
            'grinder_ratio_entry': row_dict.get('grinder_ratio', 0.0),
            'pullback_median_retrace_entry': row_dict.get('pullback_median_retrace', 0.35),
            'range_stable_pct_entry': row_dict.get('range_stable_pct', 0.0),
            'vol_pattern_entry': row_dict.get('vol_pattern', 0),
            
            # ═══════════════════════════════════════════════════════════
            # PATTERN SCORES (2)
            # ═══════════════════════════════════════════════════════════
            'dca_potential_entry': row_dict.get('dca_potential', 0),
            'scanner_score_entry': row_dict.get('score', 0.0),
            
            # ═══════════════════════════════════════════════════════════
            # WEBSOCKET METRICS (1)
            # ═══════════════════════════════════════════════════════════
            'ws_lag_ms_entry': row_dict.get('ws_lag_ms', 0) if row_dict.get('ws_lag_ms') else 0,
            
            # ═══════════════════════════════════════════════════════════
            # DERIVED FEATURES (10)
            # ═══════════════════════════════════════════════════════════
            # Depth ratios
            'depth_imbalance_entry': depth5_bid / depth5_ask if depth5_ask > 0 else 1.0,
            'depth5_total_usd_entry': depth5_bid + depth5_ask,
            'depth10_total_usd_entry': depth10_bid + depth10_ask,
            'depth_ratio_5_to_10_entry': (depth5_bid + depth5_ask) / (depth10_bid + depth10_ask) if (depth10_bid + depth10_ask) > 0 else 0.5,
            
            # Spread/depth relationships
            'spread_to_depth5_ratio_entry': row_dict.get('spread_bps', 0.0) / (depth5_bid + depth5_ask) if (depth5_bid + depth5_ask) > 0 else 0.0,
            'volume_to_depth_ratio_entry': row_dict.get('usd_per_min', 0.0) / (depth5_bid + depth5_ask) if (depth5_bid + depth5_ask) > 0 else 0.0,
            
            # Volume ratios
            'trades_per_dollar_entry': row_dict.get('trades_per_min', 0.0) / row_dict.get('usd_per_min', 1.0) if row_dict.get('usd_per_min', 0.0) > 0 else 0.0,
            'avg_trade_size_entry': row_dict.get('usd_per_min', 0.0) / row_dict.get('trades_per_min', 1.0) if row_dict.get('trades_per_min', 0.0) > 0 else 0.0,
            
            # Price context
            'mid_price_entry': (row_dict.get('bid', 0.0) + row_dict.get('ask', 0.0)) / 2.0,
            'price_precision_entry': len(str(row_dict.get('bid', 0.0)).split('.')[-1]) if '.' in str(row_dict.get('bid', 0.0)) else 0,
        }

        # ═══════════════════════════════════════════════════════════
        # PHASE 2 FEATURES (9) - Book Tracker + MM Detector
        # ═══════════════════════════════════════════════════════════
        if BOOK_ENHANCED_AVAILABLE:
            try:
                from app.services.book_tracker_enhanced import get_enhanced_book_tracker
                book_tracker = get_enhanced_book_tracker()
                book_metrics = book_tracker.get_metrics(row_dict.get('symbol', ''))
                
                if book_metrics:
                    features['spoofing_score_entry'] = book_metrics.spoofing_score
                    features['spread_stability_entry'] = book_metrics.spread_stability_score
                    features['order_lifetime_avg_entry'] = book_metrics.order_lifetime_avg
                    features['book_refresh_rate_entry'] = book_metrics.book_refresh_rate
                else:
                    features['spoofing_score_entry'] = 0.0
                    features['spread_stability_entry'] = 0.5
                    features['order_lifetime_avg_entry'] = 1.0
                    features['book_refresh_rate_entry'] = 1.0
            except Exception:
                features['spoofing_score_entry'] = 0.0
                features['spread_stability_entry'] = 0.5
                features['order_lifetime_avg_entry'] = 1.0
                features['book_refresh_rate_entry'] = 1.0
        else:
            features['spoofing_score_entry'] = 0.0
            features['spread_stability_entry'] = 0.5
            features['order_lifetime_avg_entry'] = 1.0
            features['book_refresh_rate_entry'] = 1.0
        
        if MM_DETECTOR_AVAILABLE:
            try:
                from app.services.mm_detector import get_mm_detector
                mm_detector = get_mm_detector()
                mm_pattern = mm_detector.get_pattern(row_dict.get('symbol', ''))
                
                if mm_pattern:
                    features['mm_detected_entry'] = 1
                    features['mm_confidence_entry'] = mm_pattern.mm_confidence
                    features['mm_safe_size_entry'] = mm_pattern.safe_order_size_usd
                    features['mm_lower_bound_entry'] = mm_pattern.mm_lower_bound or 0.0
                    features['mm_upper_bound_entry'] = mm_pattern.mm_upper_bound or 0.0
                else:
                    features['mm_detected_entry'] = 0
                    features['mm_confidence_entry'] = 0.0
                    features['mm_safe_size_entry'] = 50.0
                    features['mm_lower_bound_entry'] = 0.0
                    features['mm_upper_bound_entry'] = 0.0
            except Exception:
                features['mm_detected_entry'] = 0
                features['mm_confidence_entry'] = 0.0
                features['mm_safe_size_entry'] = 50.0
                features['mm_lower_bound_entry'] = 0.0
                features['mm_upper_bound_entry'] = 0.0
        else:
            features['mm_detected_entry'] = 0
            features['mm_confidence_entry'] = 0.0
            features['mm_safe_size_entry'] = 50.0
            features['mm_lower_bound_entry'] = 0.0
            features['mm_upper_bound_entry'] = 0.0
        
        return features
    
    def _save_to_db(self, record: Dict[str, Any]) -> None:
        """
        Save trade record to ml_trade_outcomes table.
        """
        db = SessionLocal()
        
        try:
            # Build INSERT query
            columns = [
                'trade_id', 'symbol', 'exchange', 'workspace_id',
                
                # Entry
                'entry_time', 'entry_price', 'entry_qty', 'entry_side',
                
                # Market features at entry
                'spread_bps_entry', 'eff_spread_bps_entry',
                'depth5_bid_usd_entry', 'depth5_ask_usd_entry',
                'depth10_bid_usd_entry', 'depth10_ask_usd_entry',
                'imbalance_entry',
                'atr1m_pct_entry', 'grinder_ratio_entry', 'pullback_median_retrace_entry',
                'trades_per_min_entry', 'usd_per_min_entry', 'median_trade_usd_entry',
                
                # Additional scanner features
                'spread_pct_entry', 'spread_abs_entry',
                'eff_spread_pct_entry', 'eff_spread_abs_entry',
                'eff_spread_maker_bps_entry', 'eff_spread_taker_bps_entry',
                'base_volume_24h_entry', 'quote_volume_24h_entry',
                'maker_fee_entry', 'taker_fee_entry', 'zero_fee_entry',
                
                # Additional candle features
                'spike_count_90m_entry', 'range_stable_pct_entry',
                'vol_pattern_entry', 'dca_potential_entry',
                
                # Scores and metrics
                'scanner_score_entry', 'ws_lag_ms_entry',
                # Derived features
                'depth_imbalance_entry', 'depth5_total_usd_entry', 'depth10_total_usd_entry',
                'depth_ratio_5_to_10_entry', 'spread_to_depth5_ratio_entry',
                'volume_to_depth_ratio_entry', 'trades_per_dollar_entry',
                'avg_trade_size_entry', 'mid_price_entry', 'price_precision_entry',
                
                # Phase 2: Book Tracker features
                'spoofing_score_entry', 'spread_stability_entry',
                'order_lifetime_avg_entry', 'book_refresh_rate_entry',
                
                # Phase 2: MM Detector features
                'mm_detected_entry', 'mm_confidence_entry', 'mm_safe_size_entry',
                'mm_lower_bound_entry', 'mm_upper_bound_entry',
                
                # Time context
                'hour_of_day', 'day_of_week', 'minute_of_hour',
                
                # Strategy params
                'take_profit_bps', 'stop_loss_bps',
                'trailing_stop_enabled', 'trail_activation_bps', 'trail_distance_bps',
                'timeout_seconds', 'exploration_mode',
                
                # Exit
                'exit_time', 'exit_price', 'exit_qty', 'exit_reason',
                
                # Outcome
                'pnl_usd', 'pnl_bps', 'pnl_percent', 'hold_duration_sec',
                
                # Performance
                'max_favorable_excursion_bps', 'max_adverse_excursion_bps',
                'peak_price', 'lowest_price',
                
                # ML labels
                'win', 'hit_tp', 'hit_sl', 'hit_trailing', 'timed_out',
                
                # Metadata
                'created_at',
            ]
            
            # Build placeholders
            placeholders = ', '.join([f':{col}' for col in columns])
            columns_str = ', '.join(columns)
            
            query = text(
                f"INSERT INTO ml_trade_outcomes ({columns_str}) "
                f"VALUES ({placeholders})"
            )
            
            # Execute
            db.execute(query, record)
            db.commit()
            
            logger.debug(f"✅ Saved to DB: {record['trade_id']}")
            
        except Exception as e:
            logger.error(f"Failed to save to DB: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get logger statistics."""
        return {
            'enabled': self.enabled,
            'trades_logged': self._trades_logged,
            'active_trades': len(self._active_trades),
            'active_symbols': list(self._active_trades.keys()),
        }


# ═══════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════

_ml_trade_logger: Optional[MLTradeLogger] = None


def get_ml_trade_logger() -> MLTradeLogger:
    """Get singleton instance of ML trade logger."""
    global _ml_trade_logger
    if _ml_trade_logger is None:
        # Load from settings
        try:
            from app.config.settings import settings
            enabled = getattr(settings, 'ML_TRADE_LOGGING_ENABLED', True)
        except:
            enabled = True
        
        _ml_trade_logger = MLTradeLogger(enabled=enabled)
    
    return _ml_trade_logger
