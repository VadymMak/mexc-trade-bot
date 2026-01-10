#!/bin/bash
# monitor_ab_test.sh
# Quick monitoring script for A/B test

echo "=========================================="
echo "⏰ A/B TEST MONITORING - $(date '+%H:%M:%S')"
echo "=========================================="

# Strategy metrics
echo -e "\n📊 STRATEGY METRICS:"
curl -s http://localhost:8000/api/strategy/metrics | python -m json.tool

# Trade stats
echo -e "\n📊 TRADE STATS:"
curl -s http://localhost:8000/api/trades/stats | python -m json.tool

# Recent trades count
echo -e "\n📊 RECENT TRADES (last 20):"
curl -s "http://localhost:8000/api/trades/recent?limit=20" | python -c "
import sys, json
trades = json.load(sys.stdin)
print(f'Total trades: {len(trades)}')
tp_count = sum(1 for t in trades if t.get('exit_reason') == 'TP')
timeout_count = sum(1 for t in trades if t.get('exit_reason') == 'TIMEOUT')
sl_count = sum(1 for t in trades if t.get('exit_reason') == 'SL')
print(f'TP: {tp_count}, TIMEOUT: {timeout_count}, SL: {sl_count}')
if len(trades) > 0:
    win_rate = (tp_count / len(trades)) * 100
    print(f'Win Rate: {win_rate:.1f}%')
"

echo "=========================================="