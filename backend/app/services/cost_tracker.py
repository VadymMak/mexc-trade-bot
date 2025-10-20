"""
Infrastructure cost tracking and net profit calculation.

Tracks monthly infrastructure costs (AWS, hosting, domain) and calculates
net profit after expenses. Used for breakeven analysis and real profitability metrics.

Usage:
    from app.services.cost_tracker import get_cost_tracker
    
    tracker = get_cost_tracker()
    analysis = tracker.calculate_net_profit(trading_pnl=16.29, period_days=1)
    print(f"Net profit: ${analysis['net_profit']}")
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


class CostTracker:
    """
    Track infrastructure costs and calculate net profit.
    
    Attributes:
        monthly_costs: Total monthly infrastructure costs in USD
        daily_cost: Daily allocation of infrastructure costs
    """
    
    def __init__(self, monthly_costs_usd: float = 80.0):
        """
        Initialize cost tracker.
        
        Args:
            monthly_costs_usd: Total monthly infrastructure costs
                              Default: $80 (AWS ~$65 + hosting ~$5 + domain ~$1 + misc ~$9)
        """
        self.monthly_costs = float(monthly_costs_usd)
        self.daily_cost = self.monthly_costs / 30.0
        
    def get_daily_allocation(self) -> float:
        """
        Get daily infrastructure cost allocation.
        
        Returns:
            Daily cost in USD (monthly_costs / 30)
        """
        return self.daily_cost
    
    def calculate_net_profit(
        self, 
        trading_pnl: float, 
        trading_fees: float = 0.0,
        period_days: int = 1
    ) -> dict:
        """
        Calculate net profit after infrastructure costs.
        
        Args:
            trading_pnl: Raw profit/loss from trading
            trading_fees: Exchange fees paid (negative value)
            period_days: Number of days in period (1 for daily, 7 for weekly, etc.)
            
        Returns:
            Dict with breakdown:
                - trading_pnl: Raw trading P&L
                - trading_fees: Exchange fees
                - infrastructure_costs: Allocated infra costs (negative)
                - net_profit: Final profit after all costs
                - breakeven_days: Days needed to cover costs
                - costs_covered: Boolean if costs are covered
        """
        infra_costs = self.daily_cost * period_days
        gross_profit = trading_pnl + trading_fees  # fees are already negative
        net_profit = gross_profit - infra_costs
        
        # Calculate breakeven days (how many days to cover monthly costs)
        if trading_pnl > 0:
            breakeven_days = int(self.monthly_costs / (trading_pnl / period_days))
        else:
            breakeven_days = 999  # Can't break even with no profit
        
        return {
            "trading_pnl": round(trading_pnl, 2),
            "trading_fees": round(trading_fees, 2),
            "gross_profit": round(gross_profit, 2),
            "infrastructure_costs": round(-infra_costs, 2),  # Negative for display
            "net_profit": round(net_profit, 2),
            "breakeven_days": min(breakeven_days, 999),
            "costs_covered": net_profit >= 0,
            "period_days": period_days
        }
    
    def get_monthly_progress(self, month_pnl: float, month_fees: float = 0.0) -> dict:
        """
        Calculate how much of monthly infrastructure costs are covered.
        
        Args:
            month_pnl: Total trading P&L for the month
            month_fees: Total fees paid this month (negative)
            
        Returns:
            Dict with monthly progress:
                - monthly_budget: Total monthly infrastructure costs
                - month_gross: P&L after fees
                - coverage_percent: % of costs covered (0-100+)
                - days_covered: Equivalent days covered
                - remaining: Amount still needed to break even
        """
        gross = month_pnl + month_fees
        coverage_pct = (gross / self.monthly_costs) * 100 if self.monthly_costs > 0 else 0
        days_covered = int((gross / self.daily_cost)) if self.daily_cost > 0 else 0
        remaining = max(0, self.monthly_costs - gross)
        
        return {
            "monthly_budget": round(self.monthly_costs, 2),
            "month_pnl": round(month_pnl, 2),
            "month_fees": round(month_fees, 2),
            "month_gross": round(gross, 2),
            "coverage_percent": min(999, round(coverage_pct, 1)),
            "days_covered": min(30, days_covered),
            "remaining": round(remaining, 2),
            "fully_covered": gross >= self.monthly_costs
        }
    
    def format_daily_report(
        self, 
        trading_pnl: float, 
        trading_fees: float = 0.0,
        trades_count: int = 0,
        win_rate: float = 0.0
    ) -> str:
        """
        Format a human-readable daily report.
        
        Args:
            trading_pnl: Daily trading P&L
            trading_fees: Daily fees
            trades_count: Number of trades today
            win_rate: Win rate percentage (0-100)
            
        Returns:
            Formatted string report
        """
        analysis = self.calculate_net_profit(trading_pnl, trading_fees, period_days=1)
        
        status_emoji = "✅" if analysis["costs_covered"] else "⚠️"
        profit_color = "+" if analysis["net_profit"] >= 0 else ""
        
        report = f"""
┌─────────────────────────────────────────────────┐
│ DAILY REPORT | {datetime.utcnow().strftime('%Y-%m-%d')}                       │
├─────────────────────────────────────────────────┤
│ Trades:            {trades_count:>6}                       │
│ Win Rate:          {win_rate:>5.1f}%                       │
│                                                 │
│ Trading P&L:       ${analysis['trading_pnl']:>8.2f}                    │
│ Trading Fees:      ${analysis['trading_fees']:>8.2f}                    │
│ Gross Profit:      ${analysis['gross_profit']:>8.2f}                    │
│                                                 │
│ Infra Costs:       ${analysis['infrastructure_costs']:>8.2f}                    │
│ ────────────────────────────────────────────────│
│ Net Profit:        ${profit_color}{analysis['net_profit']:>8.2f} {status_emoji}                │
│                                                 │
│ {status_emoji} {'Costs covered!' if analysis['costs_covered'] else f"Need ${-analysis['net_profit']:.2f} more to break even":<44} │
└─────────────────────────────────────────────────┘
        """
        return report.strip()


# Global singleton instance
_cost_tracker: Optional[CostTracker] = None


def get_cost_tracker(monthly_costs_usd: float = 80.0) -> CostTracker:
    """
    Get or create the global cost tracker instance.
    
    Args:
        monthly_costs_usd: Monthly infrastructure costs (only used on first call)
        
    Returns:
        Global CostTracker instance
    """
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = CostTracker(monthly_costs_usd)
    return _cost_tracker


# Convenience function for quick net profit calculation
def calculate_net_profit(trading_pnl: float, period_days: int = 1) -> dict:
    """
    Quick helper to calculate net profit.
    
    Args:
        trading_pnl: Trading profit/loss
        period_days: Number of days
        
    Returns:
        Net profit analysis dict
    """
    tracker = get_cost_tracker()
    return tracker.calculate_net_profit(trading_pnl, period_days=period_days)