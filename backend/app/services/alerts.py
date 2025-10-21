"""
Centralized Alert Functions
Централизованные функции для отправки алертов
"""

import logging
from typing import Optional
from app.services.telegram_bot import get_telegram_service

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# RISK ALERTS
# ═══════════════════════════════════════════════════════════

async def alert_daily_loss_limit(pnl_usd: float, limit_usd: float) -> bool:
    """
    Алерт: Достигнут дневной лимит убытков
    """
    telegram = get_telegram_service()
    
    message = (
        f"Daily P&L: <b>${pnl_usd:.2f}</b>\n"
        f"Loss Limit: ${limit_usd:.2f}\n\n"
        f"<b>Trading has been halted automatically.</b>"
    )
    
    return await telegram.send_alert(
        level="CRITICAL",
        title="🚨 Daily Loss Limit Reached",
        message=message,
        force=True  # Игнорировать quiet hours
    )


async def alert_symbol_cooldown(symbol: str, minutes: int) -> bool:
    """
    Алерт: Символ на cooldown после последовательных убытков
    """
    telegram = get_telegram_service()
    
    message = (
        f"Symbol: <b>{symbol}</b>\n"
        f"Reason: 3 consecutive losses\n"
        f"Cooldown: {minutes} minutes\n\n"
        f"Trading paused for this symbol."
    )
    
    return await telegram.send_alert(
        level="WARNING",
        title=f"⚠️ Cooldown: {symbol}",
        message=message,
        force=False
    )


async def alert_trading_resumed() -> bool:
    """
    Алерт: Торговля возобновлена
    """
    telegram = get_telegram_service()
    
    message = "System is ready for trading."
    
    return await telegram.send_alert(
        level="INFO",
        title="✅ Trading Resumed",
        message=message,
        force=False
    )


# ═══════════════════════════════════════════════════════════
# SYSTEM ALERTS
# ═══════════════════════════════════════════════════════════

async def alert_ws_disconnect(provider: str, duration_sec: int) -> bool:
    """
    Алерт: WebSocket отключен
    """
    telegram = get_telegram_service()
    
    message = (
        f"Provider: <b>{provider}</b>\n"
        f"Disconnected for: {duration_sec} seconds\n\n"
        f"Attempting to reconnect..."
    )
    
    return await telegram.send_alert(
        level="ERROR",
        title="🔴 WebSocket Disconnected",
        message=message,
        force=True  # Критичная ошибка
    )


async def alert_system_error(module: str, error: str, traceback: str = "") -> bool:
    """
    Алерт: Системная ошибка
    """
    telegram = get_telegram_service()
    
    # Обрезать traceback если слишком длинный
    tb_preview = traceback[:500] if traceback else "N/A"
    
    message = (
        f"Module: <code>{module}</code>\n"
        f"Error: <code>{error}</code>\n\n"
        f"Traceback:\n<pre>{tb_preview}</pre>"
    )
    
    return await telegram.send_alert(
        level="CRITICAL",
        title="🚨 System Error",
        message=message,
        force=True  # Критичная ошибка
    )


async def alert_emergency_stop(positions_closed: int) -> bool:
    """
    Алерт: Аварийная остановка активирована
    """
    telegram = get_telegram_service()
    
    message = (
        f"All trading has been halted.\n"
        f"Positions closed: {positions_closed}\n\n"
        f"<b>Manual intervention required.</b>"
    )
    
    return await telegram.send_alert(
        level="CRITICAL",
        title="🚨 EMERGENCY STOP",
        message=message,
        force=True  # Всегда отправлять
    )


# ═══════════════════════════════════════════════════════════
# PERFORMANCE ALERTS (опционально)
# ═══════════════════════════════════════════════════════════

async def alert_profit_target(pnl_usd: float, target_usd: float) -> bool:
    """
    Алерт: Достигнута целевая прибыль
    """
    telegram = get_telegram_service()
    
    message = (
        f"Daily P&L: <b>${pnl_usd:.2f}</b>\n"
        f"Target: ${target_usd:.2f}\n\n"
        f"🎉 <b>Great job!</b>"
    )
    
    return await telegram.send_alert(
        level="INFO",
        title="🎉 Daily Profit Target Reached",
        message=message,
        force=False
    )


async def alert_win_rate_drop(win_rate: float, threshold: float) -> bool:
    """
    Алерт: Win rate упал ниже порога
    """
    telegram = get_telegram_service()
    
    message = (
        f"Current Win Rate: <b>{win_rate:.1f}%</b>\n"
        f"Threshold: {threshold:.1f}%\n\n"
        f"Review strategy parameters."
    )
    
    return await telegram.send_alert(
        level="WARNING",
        title="📉 Win Rate Drop",
        message=message,
        force=False
    )


# ═══════════════════════════════════════════════════════════
# TEST ALERT
# ═══════════════════════════════════════════════════════════

async def send_test_alert() -> bool:
    """
    Отправить тестовый алерт
    """
    telegram = get_telegram_service()
    
    message = "This is a test alert from your trading bot."
    
    return await telegram.send_alert(
        level="INFO",
        title="🧪 Test Alert",
        message=message,
        force=False
    )