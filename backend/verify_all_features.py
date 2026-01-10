#!/usr/bin/env python3
"""
Comprehensive ML Feature Verification Script
Checks all 50+ features logged in ml_trade_outcomes table
"""

import sqlite3
import sys

def main():
    conn = sqlite3.connect('mexc.db')
    cursor = conn.cursor()
    
    # Get ALL columns
    cursor.execute('PRAGMA table_info(ml_trade_outcomes)')
    columns = [col[1] for col in cursor.fetchall()]
    print(f'\n{"="*80}')
    print(f'DATABASE INFO')
    print(f'{"="*80}')
    print(f'Total columns in table: {len(columns)}')
    
    # Get latest trade
    cursor.execute('SELECT * FROM ml_trade_outcomes ORDER BY id DESC LIMIT 1')
    row = cursor.fetchone()
    
    if not row:
        print('\nERROR: No trades found!')
        print('Please execute at least 1 trade first.')
        conn.close()
        return
    
    # Map values to column names
    trade_data = dict(zip(columns, row))
    
    # Define feature groups
    feature_groups = {
        'Base Spread': [
            'spread_bps_entry', 'spread_pct_entry', 'spread_abs_entry'
        ],
        'Effective Spreads': [
            'eff_spread_bps_entry', 'eff_spread_pct_entry', 
            'eff_spread_maker_bps_entry', 'eff_spread_taker_bps_entry',
            'eff_spread_abs_entry'
        ],
        'Depth Features': [
            'depth5_bid_usd_entry', 'depth5_ask_usd_entry',
            'depth10_bid_usd_entry', 'depth10_ask_usd_entry'
        ],
        'Volume Metrics': [
            'base_volume_24h_entry', 'quote_volume_24h_entry',
            'trades_per_min_entry', 'usd_per_min_entry', 'median_trade_usd_entry'
        ],
        'Fee Structure': [
            'maker_fee_entry', 'taker_fee_entry', 'zero_fee_entry'
        ],
        'Volatility (Candles)': [
            'atr1m_pct_entry', 'spike_count_90m_entry', 'grinder_ratio_entry',
            'pullback_median_retrace_entry', 'range_stable_pct_entry', 'vol_pattern_entry'
        ],
        'Pattern Scores': [
            'dca_potential_entry', 'scanner_score_entry', 'imbalance_entry'
        ],
        'Derived Features': [
            'depth_imbalance_entry', 'depth5_total_usd_entry', 'depth10_total_usd_entry',
            'depth_ratio_5_to_10_entry', 'spread_to_depth5_ratio_entry',
            'volume_to_depth_ratio_entry', 'trades_per_dollar_entry',
            'avg_trade_size_entry', 'mid_price_entry', 'price_precision_entry'
        ],
        'WebSocket': [
            'ws_lag_ms_entry'
        ],
    }
    
    print(f'\n{"="*80}')
    print(f'FEATURE VERIFICATION - LATEST TRADE')
    print(f'{"="*80}')
    print(f'Symbol: {trade_data.get("symbol", "N/A")}')
    print(f'Entry Time: {trade_data.get("entry_time", "N/A")}')
    print(f'Exit Reason: {trade_data.get("exit_reason", "N/A")}')
    print(f'PnL (bps): {trade_data.get("pnl_bps", 0):.2f}')
    print(f'{"="*80}')
    
    total_features = 0
    non_zero_features = 0
    
    for group_name, feature_list in feature_groups.items():
        print(f'\n[{group_name}]')
        group_non_zero = 0
        
        for feature in feature_list:
            if feature in trade_data:
                value = trade_data[feature]
                total_features += 1
                
                # Check if non-zero/non-null
                is_active = False
                if value is not None:
                    try:
                        # For boolean/integer flags, any value counts
                        if feature == 'zero_fee_entry':
                            is_active = True
                        else:
                            is_active = abs(float(value)) > 0.00001
                    except (ValueError, TypeError):
                        is_active = bool(value)
                
                if is_active:
                    non_zero_features += 1
                    group_non_zero += 1
                    status = 'OK'
                else:
                    status = 'ZERO'
                
                # Format value
                if isinstance(value, float):
                    if abs(value) > 1000:
                        val_str = f'{value:,.2f}'
                    elif abs(value) > 1:
                        val_str = f'{value:.4f}'
                    else:
                        val_str = f'{value:.6f}'
                else:
                    val_str = str(value)
                
                print(f'  [{status:^5}] {feature:38s} = {val_str}')
        
        print(f'  --> Active: {group_non_zero}/{len(feature_list)}')
    
    print(f'\n{"="*80}')
    print(f'SUMMARY')
    print(f'{"="*80}')
    print(f'Total features checked:  {total_features}')
    print(f'Non-zero features:       {non_zero_features}')
    print(f'Coverage:                {non_zero_features/total_features*100:.1f}%')
    print(f'{"="*80}')
    
    if non_zero_features >= 40:
        print('\nRESULT: EXCELLENT! 40+ features working!')
        print('Ready for production data collection!')
    elif non_zero_features >= 30:
        print('\nRESULT: GOOD! 30+ features working!')
        print('Sufficient for ML training.')
    elif non_zero_features >= 20:
        print('\nRESULT: FAIR - 20+ features, but could be better')
    else:
        print('\nRESULT: POOR - Too few features active')
    
    print(f'\n{"="*80}')
    print('NEXT STEPS:')
    if non_zero_features >= 30:
        print('1. Start production: 5 symbols for 5-7 days')
        print('2. Collect 20,000 trades')
        print('3. Train ML model v2 with full feature set')
    else:
        print('1. Check logs for errors')
        print('2. Verify scanner is returning all fields')
        print('3. Verify candles_cache is populated')
    print(f'{"="*80}\n')
    
    conn.close()

if __name__ == '__main__':
    main()