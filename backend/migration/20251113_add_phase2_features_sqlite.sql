-- Add Phase 2 features to ml_trade_outcomes table
-- Date: 2025-11-13
-- Phase 2: Book Tracker + MM Detector features

-- Book Tracker features (4 columns)
ALTER TABLE ml_trade_outcomes ADD COLUMN spoofing_score_entry REAL DEFAULT 0.0;
ALTER TABLE ml_trade_outcomes ADD COLUMN spread_stability_entry REAL DEFAULT 0.5;
ALTER TABLE ml_trade_outcomes ADD COLUMN order_lifetime_avg_entry REAL DEFAULT 1.0;
ALTER TABLE ml_trade_outcomes ADD COLUMN book_refresh_rate_entry REAL DEFAULT 1.0;

-- MM Detector features (5 columns)
ALTER TABLE ml_trade_outcomes ADD COLUMN mm_detected_entry INTEGER DEFAULT 0;
ALTER TABLE ml_trade_outcomes ADD COLUMN mm_confidence_entry REAL DEFAULT 0.0;
ALTER TABLE ml_trade_outcomes ADD COLUMN mm_safe_size_entry REAL DEFAULT 50.0;
ALTER TABLE ml_trade_outcomes ADD COLUMN mm_lower_bound_entry REAL DEFAULT 0.0;
ALTER TABLE ml_trade_outcomes ADD COLUMN mm_upper_bound_entry REAL DEFAULT 0.0;

-- Create indexes for new features
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_mm_detected ON ml_trade_outcomes(mm_detected_entry);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_spoofing ON ml_trade_outcomes(spoofing_score_entry);