"""
Daily Report Generator
Генератор дневных отчётов P&L
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.trades import Trade

logger = logging.getLogger(__name__)


class DailyReportGenerator:
    """
    Генератор дневных отчётов
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    async def generate_report(
        self,
        date: Optional[datetime] = None,
        exchange: str = "MEXC"
    ) -> str:
        """
        Сгенерировать дневной отчёт
        
        Args:
            date: Дата отчёта (default: сегодня UTC)
            exchange: Биржа
            
        Returns:
            HTML форматированный отчёт для Telegram
        """
        if date is None:
            date = datetime.now(timezone.utc)
        
        # Начало и конец дня (UTC)
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Получить все закрытые трейды за день
        trades = self.db.query(Trade).filter(
            Trade.exchange == exchange,
            Trade.status == "CLOSED",
            Trade.exit_time >= start_of_day,
            Trade.exit_time <= end_of_day
        ).all()
        
        if not trades:
            return self._generate_empty_report(date)
        
        # Вычислить метрики
        metrics = self._calculate_metrics(trades)
        
        # Топ символов
        top_symbols = self._get_top_symbols(trades, limit=3)
        
        # Форматировать отчёт
        report = self._format_report(date, metrics, top_symbols)
        
        return report
    
    def _calculate_metrics(self, trades: List[Trade]) -> Dict:
        """
        Вычислить метрики из списка трейдов
        """
        total_trades = len(trades)
        
        wins = [t for t in trades if t.pnl_usd and t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd and t.pnl_usd < 0]
        breakevens = [t for t in trades if t.pnl_usd and t.pnl_usd == 0]
        
        win_count = len(wins)
        loss_count = len(losses)
        
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0
        
        # P&L расчёты
        gross_pnl = sum(t.pnl_usd for t in trades if t.pnl_usd)
        total_fees = sum((t.entry_fee or 0) + (t.exit_fee or 0) for t in trades)
        net_pnl = gross_pnl - total_fees
        
        # Средние значения
        avg_pnl = gross_pnl / total_trades if total_trades > 0 else 0.0
        
        durations = [
            (t.exit_time - t.entry_time).total_seconds()
            for t in trades
            if t.exit_time and t.entry_time
        ]
        avg_duration = sum(durations) / len(durations) if durations else 0.0
        
        # Лучший и худший
        best_trade = max(trades, key=lambda t: t.pnl_usd or 0)
        worst_trade = min(trades, key=lambda t: t.pnl_usd or 0)
        
        return {
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "breakeven_count": len(breakevens),
            "win_rate": win_rate,
            "gross_pnl": gross_pnl,
            "total_fees": total_fees,
            "net_pnl": net_pnl,
            "avg_pnl": avg_pnl,
            "avg_duration": avg_duration,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
        }
    
    def _get_top_symbols(self, trades: List[Trade], limit: int = 3) -> List[Dict]:
        """
        Получить топ символов по прибыли
        """
        # Группировка по символам
        symbol_stats = {}
        
        for trade in trades:
            symbol = trade.symbol
            if symbol not in symbol_stats:
                symbol_stats[symbol] = {
                    "symbol": symbol,
                    "count": 0,
                    "pnl": 0.0
                }
            
            symbol_stats[symbol]["count"] += 1
            symbol_stats[symbol]["pnl"] += trade.pnl_usd or 0.0
        
        # Сортировка по PnL
        sorted_symbols = sorted(
            symbol_stats.values(),
            key=lambda x: x["pnl"],
            reverse=True
        )
        
        return sorted_symbols[:limit]
    
    def _format_report(
        self,
        date: datetime,
        metrics: Dict,
        top_symbols: List[Dict]
    ) -> str:
        """
        Форматировать отчёт в HTML для Telegram
        """
        date_str = date.strftime("%Y-%m-%d")
        
        # Эмодзи для P&L
        pnl_emoji = "📈" if metrics["net_pnl"] > 0 else "📉" if metrics["net_pnl"] < 0 else "➖"
        
        report = (
            f"📊 <b>DAILY REPORT</b> - {date_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            
            f"{pnl_emoji} <b>Net P&L: ${metrics['net_pnl']:+.2f}</b>\n"
            f"💰 Gross P&L: ${metrics['gross_pnl']:+.2f}\n"
            f"💸 Fees: ${metrics['total_fees']:.2f}\n\n"
            
            f"📈 <b>Performance:</b>\n"
            f"• Trades: {metrics['total_trades']} "
            f"({metrics['win_count']}W / {metrics['loss_count']}L / {metrics['breakeven_count']}BE)\n"
            f"• Win Rate: <b>{metrics['win_rate']:.1f}%</b>\n"
            f"• Avg P&L/Trade: ${metrics['avg_pnl']:+.2f}\n"
            f"• Avg Duration: {metrics['avg_duration']:.1f}s\n\n"
            
            f"💎 <b>Best Trade:</b> +${metrics['best_trade'].pnl_usd:.2f} "
            f"({metrics['best_trade'].symbol})\n"
            f"💔 <b>Worst Trade:</b> ${metrics['worst_trade'].pnl_usd:.2f} "
            f"({metrics['worst_trade'].symbol})\n"
        )
        
        # Топ символов
        if top_symbols:
            report += f"\n🎯 <b>Top Performers:</b>\n"
            for i, sym in enumerate(top_symbols, 1):
                report += (
                    f"{i}. <b>{sym['symbol']}</b>: "
                    f"${sym['pnl']:+.2f} ({sym['count']} trades)\n"
                )
        
        # Футер
        tomorrow = date.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = tomorrow.replace(day=tomorrow.day + 1)
        report += (
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Next report: {tomorrow.strftime('%Y-%m-%d')}, 00:00 UTC</i>"
        )
        
        return report
    
    def _generate_empty_report(self, date: datetime) -> str:
        """
        Отчёт для дня без трейдов
        """
        date_str = date.strftime("%Y-%m-%d")
        
        return (
            f"📊 <b>DAILY REPORT</b> - {date_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"No trades executed today.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>System is operational.</i>"
        )


async def generate_and_send_daily_report(db: Session) -> bool:
    """
    Сгенерировать и отправить дневной отчёт через Telegram
    
    Args:
        db: Database session
        
    Returns:
        True если успешно отправлено
    """
    try:
        # Генерация отчёта
        generator = DailyReportGenerator(db)
        report = await generator.generate_report()
        
        # Отправка через Telegram
        from app.services.telegram_bot import get_telegram_service
        telegram = get_telegram_service()
        
        success = await telegram.send_message(
            text=report,
            parse_mode='HTML'
        )
        
        if success:
            logger.info("✅ Daily report sent successfully")
        else:
            logger.error("❌ Failed to send daily report")
        
        return success
    
    except Exception as e:
        logger.error(f"Error generating daily report: {e}")
        return False