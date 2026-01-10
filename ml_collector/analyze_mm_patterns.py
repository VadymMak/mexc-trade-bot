#!/usr/bin/env python3
"""
Analyze MM bot patterns from ml_trade_outcomes database.

This script estimates MM bot sizes and analyzes correlation
with trade outcomes (timeout rate, win rate, hold time).

Usage:
    python analyze_mm_patterns.py
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# Database path (same folder)
DB_PATH = Path(__file__).parent.parent / "backend" / "mexc.db"

def load_trade_data(conn):
    """Load trade data from ml_trade_outcomes table."""
    query = """
        SELECT 
            symbol,
            median_trade_usd_entry,
            pnl_bps,
            hold_duration_sec,
            timed_out,
            exit_reason,
            win,
            entry_time,
            exploration_mode
        FROM ml_trade_outcomes
        WHERE median_trade_usd_entry IS NOT NULL
          AND median_trade_usd_entry > 0
        ORDER BY entry_time DESC
    """
    
    try:
        df = pd.read_sql(query, conn)
        return df
    except Exception as e:
        print(f"❌ Error loading data: {e}")
        print("\nMake sure ml_trade_outcomes table exists and has data.")
        return None


def estimate_mm_size(median_trade_usd: float) -> float:
    """
    Estimate MM bot typical size based on median trade.
    
    MM bots typically trade 5-10x larger than median retail trade.
    We use 8x as empirical coefficient.
    """
    mm_size = median_trade_usd * 8.0
    
    # Reasonable bounds
    mm_size = max(mm_size, 500.0)   # Min $500
    mm_size = min(mm_size, 5000.0)  # Max $5000
    
    return mm_size


def analyze_by_symbol(df, position_size=50.0):
    """Analyze MM bot patterns grouped by symbol."""
    
    results = []
    
    print("=" * 70)
    print("MM BOT SIZE ESTIMATION BY SYMBOL")
    print("=" * 70)
    print()
    
    for symbol in sorted(df['symbol'].unique()):
        symbol_df = df[df['symbol'] == symbol]
        
        # Estimate MM bot size
        median_trade = symbol_df['median_trade_usd_entry'].median()
        mm_size = estimate_mm_size(median_trade)
        
        # Calculate metrics
        total = len(symbol_df)
        wins = symbol_df['win'].sum()
        timeouts = symbol_df['timed_out'].sum()
        
        win_rate = (wins / total * 100) if total > 0 else 0
        timeout_rate = (timeouts / total * 100) if total > 0 else 0
        avg_hold = symbol_df['hold_duration_sec'].mean()
        avg_pnl = symbol_df[symbol_df['win'] == 1]['pnl_bps'].mean() if wins > 0 else 0
        
        # Position ratio
        position_ratio = position_size / mm_size
        
        # Determine status
        if position_ratio < 0.15:
            status = "✅ GOOD"
        elif position_ratio < 0.25:
            status = "⚠️  WARN"
        else:
            status = "❌ BAD"
        
        # Store results
        results.append({
            'symbol': symbol,
            'trades': total,
            'median_trade': median_trade,
            'mm_size': mm_size,
            'position_ratio': position_ratio,
            'win_rate': win_rate,
            'timeout_rate': timeout_rate,
            'avg_hold': avg_hold,
            'avg_pnl': avg_pnl
        })
        
        # Print symbol analysis
        print(f"{symbol:12s} {status}")
        print(f"  Median trade:      ${median_trade:7.0f}")
        print(f"  Est. MM bot size:  ${mm_size:7.0f}")
        print(f"  Position ratio:    {position_ratio:6.1%} (${position_size:.0f} / ${mm_size:.0f})")
        print(f"  Trades:            {total:5d}")
        print(f"  Win rate:          {win_rate:6.1f}%")
        print(f"  Timeout rate:      {timeout_rate:6.1f}%")
        print(f"  Avg hold time:     {avg_hold:6.1f}s")
        print(f"  Avg win (bps):     {avg_pnl:6.2f}")
        print()
    
    return pd.DataFrame(results)


def correlation_analysis(results_df):
    """Analyze correlations between position ratio and outcomes."""
    
    print("=" * 70)
    print("CORRELATION ANALYSIS")
    print("=" * 70)
    print()
    
    # Position ratio vs win rate
    print("📊 Position Ratio vs Win Rate:")
    corr_win = results_df['position_ratio'].corr(results_df['win_rate'])
    print(f"   Correlation: {corr_win:+.3f}")
    
    if corr_win < -0.5:
        print("   ⚠️  Strong NEGATIVE correlation (larger size → lower win rate)")
    elif corr_win < -0.3:
        print("   ⚠️  Moderate negative correlation")
    else:
        print("   ℹ️  Weak or no correlation")
    print()
    
    # Position ratio vs timeout rate
    print("📊 Position Ratio vs Timeout Rate:")
    corr_timeout = results_df['position_ratio'].corr(results_df['timeout_rate'])
    print(f"   Correlation: {corr_timeout:+.3f}")
    
    if corr_timeout > 0.5:
        print("   ⚠️  Strong POSITIVE correlation (larger size → more timeouts)")
    elif corr_timeout > 0.3:
        print("   ⚠️  Moderate positive correlation")
    else:
        print("   ℹ️  Weak or no correlation")
    print()
    
    # Position ratio vs hold time
    print("📊 Position Ratio vs Avg Hold Time:")
    corr_hold = results_df['position_ratio'].corr(results_df['avg_hold'])
    print(f"   Correlation: {corr_hold:+.3f}")
    
    if corr_hold > 0.3:
        print("   ⚠️  Positive correlation (larger size → longer holds)")
    else:
        print("   ℹ️  Weak or no correlation")
    print()
    
    return {
        'win_rate': corr_win,
        'timeout_rate': corr_timeout,
        'hold_time': corr_hold
    }


def grouped_analysis(results_df):
    """Analyze metrics grouped by position ratio ranges."""
    
    print("=" * 70)
    print("GROUPED ANALYSIS (by Position Ratio)")
    print("=" * 70)
    print()
    
    # Create ratio groups
    results_df['ratio_group'] = pd.cut(
        results_df['position_ratio'],
        bins=[0, 0.10, 0.20, 0.30, 1.0],
        labels=['<10%', '10-20%', '20-30%', '>30%']
    )
    
    # Group by ratio
    grouped = results_df.groupby('ratio_group', observed=True).agg({
        'symbol': 'count',
        'trades': 'sum',
        'win_rate': 'mean',
        'timeout_rate': 'mean',
        'avg_hold': 'mean',
        'avg_pnl': 'mean'
    }).round(2)
    
    grouped.columns = ['Symbols', 'Total Trades', 'Avg Win %', 'Avg Timeout %', 'Avg Hold (s)', 'Avg PnL (bps)']
    
    print(grouped)
    print()


def generate_recommendation(correlations):
    """Generate recommendation based on correlation analysis."""
    
    print("=" * 70)
    print("RECOMMENDATION FOR DATASET #2")
    print("=" * 70)
    print()
    
    timeout_corr = correlations['timeout_rate']
    win_corr = correlations['win_rate']
    
    # Strong evidence
    if timeout_corr > 0.6 or win_corr < -0.5:
        print("🔴 STRONG EVIDENCE: MM-aware sizing is HIGHLY RECOMMENDED!")
        print()
        print("   Your position size relative to MM bot size significantly affects:")
        print(f"   • Timeout rate (correlation: {timeout_corr:+.2f})")
        print(f"   • Win rate (correlation: {win_corr:+.2f})")
        print()
        print("   ✅ ACTION: Implement MM-aware dynamic position sizing for dataset #2")
        print("   ✅ Expected improvement: +2-4% win rate, -3-5% timeout rate")
        print()
        
    # Moderate evidence
    elif timeout_corr > 0.3 or win_corr < -0.3:
        print("🟡 MODERATE EVIDENCE: MM-aware sizing may help")
        print()
        print("   There is some correlation between position size and outcomes:")
        print(f"   • Timeout rate (correlation: {timeout_corr:+.2f})")
        print(f"   • Win rate (correlation: {win_corr:+.2f})")
        print()
        print("   ✅ ACTION: Consider A/B testing (50% static, 50% mm-aware)")
        print("   ✅ Expected improvement: +1-2% win rate, -1-2% timeout rate")
        print()
        
    # Weak evidence
    else:
        print("🟢 WEAK EVIDENCE: MM-aware sizing not critical")
        print()
        print("   Position size ratio shows weak correlation with outcomes:")
        print(f"   • Timeout rate (correlation: {timeout_corr:+.2f})")
        print(f"   • Win rate (correlation: {win_corr:+.2f})")
        print()
        print("   ℹ️  ACTION: Focus on other optimizations (ML model, risk management)")
        print("   ℹ️  MM-aware sizing may still be added as incremental improvement")
        print()


def main():
    """Main analysis function."""
    
    print()
    print("=" * 70)
    print("   MM BOT PATTERN ANALYSIS")
    print("   Anton Klevtsov Method: Position Size vs MM Bot Size")
    print("=" * 70)
    print()
    
    # Check if database exists
    if not DB_PATH.exists():
        print(f"❌ Database not found: {DB_PATH}")
        print("\nPlease run the bot first to collect trade data.")
        return
    
    # Connect to database
    try:
        conn = sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        return
    
    # Load data
    print(f"📂 Loading data from: {DB_PATH.name}")
    df = load_trade_data(conn)
    
    if df is None or len(df) == 0:
        print("❌ No trade data found in ml_trade_outcomes table.")
        conn.close()
        return
    
    print(f"✅ Loaded {len(df)} trades from {len(df['symbol'].unique())} symbols")
    print(f"📅 Date range: {df['entry_time'].min()} to {df['entry_time'].max()}")
    print()
    
    # Get position size
    POSITION_SIZE = 50.0  # Your current position size
    print(f"💰 Current position size: ${POSITION_SIZE}")
    print()
    
    # Analyze by symbol
    results_df = analyze_by_symbol(df, POSITION_SIZE)
    
    # Correlation analysis
    correlations = correlation_analysis(results_df)
    
    # Grouped analysis
    grouped_analysis(results_df)
    
    # Generate recommendation
    generate_recommendation(correlations)
    
    # Additional stats
    print("=" * 70)
    print("OVERALL STATISTICS")
    print("=" * 70)
    print()
    print(f"Total trades:          {len(df):,}")
    print(f"Total symbols:         {len(df['symbol'].unique())}")
    print(f"Overall win rate:      {df['win'].mean() * 100:.1f}%")
    print(f"Overall timeout rate:  {df['timed_out'].mean() * 100:.1f}%")
    print(f"Avg hold time:         {df['hold_duration_sec'].mean():.1f}s")
    print(f"Exploration trades:    {(df['exploration_mode'] == 1).sum()} ({(df['exploration_mode'] == 1).mean() * 100:.1f}%)")
    print()
    
    # Close connection
    conn.close()
    
    print("=" * 70)
    print("Analysis complete!")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()