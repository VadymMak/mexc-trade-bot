import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

all_features = [
    'spread_bps_entry', 'spread_pct_entry', 'spread_abs_entry',
    'eff_spread_bps_entry', 'eff_spread_pct_entry', 'eff_spread_abs_entry',
    'eff_spread_maker_bps_entry', 'eff_spread_taker_bps_entry',
    'imbalance_entry',
    'depth5_bid_usd_entry', 'depth5_ask_usd_entry',
    'depth10_bid_usd_entry', 'depth10_ask_usd_entry',
    'base_volume_24h_entry', 'quote_volume_24h_entry',
    'trades_per_min_entry', 'usd_per_min_entry', 'median_trade_usd_entry',
    'maker_fee_entry', 'taker_fee_entry', 'zero_fee_entry',
    'atr1m_pct_entry', 'spike_count_90m_entry', 'grinder_ratio_entry',
    'pullback_median_retrace_entry', 'range_stable_pct_entry', 'vol_pattern_entry',
    'dca_potential_entry', 'scanner_score_entry', 'ws_lag_ms_entry',
    'depth_imbalance_entry', 'depth5_total_usd_entry', 'depth10_total_usd_entry',
    'depth_ratio_5_to_10_entry', 'spread_to_depth5_ratio_entry',
    'volume_to_depth_ratio_entry', 'trades_per_dollar_entry',
    'avg_trade_size_entry', 'mid_price_entry', 'price_precision_entry'
]

print(f'Total feature columns: {len(all_features)}')

query = f"SELECT {', '.join(all_features)} FROM ml_trade_outcomes WHERE trades_per_min_entry > 0 ORDER BY id DESC LIMIT 1"

cursor.execute(query)
row = cursor.fetchone()

non_zero = 0
for i, val in enumerate(row):
    feat = all_features[i]
    if val is not None and (feat == 'zero_fee_entry' or abs(float(val)) > 0.00001):
        non_zero += 1

print(f'Non-zero features: {non_zero}/{len(all_features)}')
print(f'Coverage: {non_zero/len(all_features)*100:.1f}%')

if non_zero >= 35:
    print('EXCELLENT! 35+ features!')
elif non_zero >= 25:
    print('GOOD! 25+ features!')
else:
    print('Need more features')

conn.close()
