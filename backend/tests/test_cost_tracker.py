import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# В Python console или создайте test_cost_tracker.py:
from app.services.cost_tracker import get_cost_tracker

tracker = get_cost_tracker(monthly_costs_usd=80.0)

# Test 1: Daily calculation
result = tracker.calculate_net_profit(trading_pnl=16.29, period_days=1)
print("Daily analysis:", result)
# Expected: net_profit should be ~13.62 (16.29 - 2.67)

# Test 2: Weekly calculation
result = tracker.calculate_net_profit(trading_pnl=100.0, period_days=7)
print("Weekly analysis:", result)

# Test 3: Monthly progress
progress = tracker.get_monthly_progress(month_pnl=320.0, month_fees=-15.0)
print("Monthly progress:", progress)
# Expected: coverage_percent should be ~381% (305/80*100)

# Test 4: Daily report
report = tracker.format_daily_report(
    trading_pnl=16.29,
    trading_fees=-0.50,
    trades_count=234,
    win_rate=68.0
)
print(report)
