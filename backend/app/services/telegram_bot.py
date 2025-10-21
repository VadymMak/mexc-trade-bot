"""
Telegram Alert Service
Отправка уведомлений через Telegram бота
"""

import logging
import os
from datetime import datetime, time as dt_time, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Импорт telegram библиотеки
try:
    from telegram import Bot
    from telegram.error import TelegramError
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed, Telegram alerts disabled")


class TelegramAlertService:
    """
    Сервис для отправки алертов через Telegram
    """
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
        quiet_hours_enabled: bool = False,
        quiet_hours_start: str = "23:00",
        quiet_hours_end: str = "07:00"
    ):
        self.enabled = enabled and _TELEGRAM_AVAILABLE
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        
        # Quiet hours
        self.quiet_hours_enabled = quiet_hours_enabled
        self.quiet_hours_start = quiet_hours_start
        self.quiet_hours_end = quiet_hours_end
        
        # Инициализация бота
        self.bot: Optional[Bot] = None
        if self.enabled and self.bot_token:
            try:
                self.bot = Bot(token=self.bot_token)
                logger.info(f"✅ Telegram bot initialized (chat_id: {self.chat_id})")
            except Exception as e:
                logger.error(f"Failed to initialize Telegram bot: {e}")
                self.enabled = False
        else:
            if not _TELEGRAM_AVAILABLE:
                logger.warning("Telegram library not available")
            elif not self.bot_token:
                logger.warning("TELEGRAM_BOT_TOKEN not set, alerts disabled")
            else:
                logger.info("Telegram alerts disabled by config")
    
    def is_enabled(self) -> bool:
        """Проверить включены ли алерты"""
        return self.enabled and self.bot is not None
    
    def is_quiet_hours(self) -> bool:
        """
        Проверить находимся ли в тихих часах
        """
        if not self.quiet_hours_enabled:
            return False
        
        try:
            now = datetime.now(timezone.utc).time()
            
            # Парсинг времени
            start_h, start_m = map(int, self.quiet_hours_start.split(':'))
            end_h, end_m = map(int, self.quiet_hours_end.split(':'))
            
            start_time = dt_time(start_h, start_m)
            end_time = dt_time(end_h, end_m)
            
            # Проверка диапазона
            if start_time <= end_time:
                # Обычный диапазон (например, 23:00-07:00 неправильно, но 08:00-22:00 правильно)
                return start_time <= now <= end_time
            else:
                # Диапазон через полночь (например, 23:00-07:00)
                return now >= start_time or now <= end_time
        
        except Exception as e:
            logger.error(f"Error checking quiet hours: {e}")
            return False
    
    async def send_message(
        self,
        text: str,
        parse_mode: str = 'HTML',
        disable_notification: bool = False
    ) -> bool:
        """
        Отправить сообщение в Telegram
        
        Args:
            text: Текст сообщения
            parse_mode: Формат (HTML или Markdown)
            disable_notification: Тихое уведомление
            
        Returns:
            True если успешно отправлено
        """
        if not self.is_enabled():
            logger.debug("Telegram alerts disabled, message not sent")
            return False
        
        if not self.chat_id:
            logger.error("TELEGRAM_CHAT_ID not set")
            return False
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
                disable_notification=disable_notification
            )
            logger.debug(f"✅ Telegram message sent: {text[:50]}...")
            return True
        
        except TelegramError as e:
            logger.error(f"Telegram error: {e}")
            return False
        
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
    
    async def send_alert(
        self,
        level: str,
        title: str,
        message: str,
        force: bool = False
    ) -> bool:
        """
        Отправить форматированный алерт
        
        Args:
            level: Уровень (INFO, WARNING, ERROR, CRITICAL)
            title: Заголовок
            message: Сообщение
            force: Игнорировать quiet hours (для критичных)
            
        Returns:
            True если успешно отправлено
        """
        if not self.is_enabled():
            return False
        
        # Проверка quiet hours (кроме force)
        if not force and self.is_quiet_hours():
            logger.debug(f"Quiet hours active, alert suppressed: {title}")
            return False
        
        # Эмодзи по уровням
        emoji_map = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ERROR": "🔴",
            "CRITICAL": "🚨"
        }
        
        emoji = emoji_map.get(level.upper(), "📢")
        
        # Форматирование сообщения
        formatted = (
            f"{emoji} <b>{title}</b>\n\n"
            f"{message}\n\n"
            f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        )
        
        return await self.send_message(formatted, parse_mode='HTML')
    
    async def test_connection(self) -> bool:
        """
        Проверить подключение к боту
        """
        if not self.is_enabled():
            logger.error("Telegram not enabled")
            return False
        
        try:
            me = await self.bot.get_me()
            logger.info(f"✅ Telegram bot connected: @{me.username}")
            
            # Отправить тестовое сообщение
            test_message = (
                "✅ <b>Telegram Bot Connected</b>\n\n"
                f"Bot: @{me.username}\n"
                f"Chat ID: {self.chat_id}\n"
                f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            
            return await self.send_message(test_message)
        
        except Exception as e:
            logger.error(f"Telegram connection test failed: {e}")
            return False


# ═══════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════

_telegram_service: Optional[TelegramAlertService] = None


def get_telegram_service() -> TelegramAlertService:
    """Получить глобальный экземпляр сервиса (singleton)"""
    global _telegram_service
    if _telegram_service is None:
        # Загрузка из ENV
        enabled = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
        quiet_enabled = os.getenv("TELEGRAM_QUIET_HOURS_ENABLED", "false").lower() == "true"
        quiet_start = os.getenv("TELEGRAM_QUIET_HOURS_START", "23:00")
        quiet_end = os.getenv("TELEGRAM_QUIET_HOURS_END", "07:00")
        
        _telegram_service = TelegramAlertService(
            enabled=enabled,
            quiet_hours_enabled=quiet_enabled,
            quiet_hours_start=quiet_start,
            quiet_hours_end=quiet_end
        )
    
    return _telegram_service


async def test_telegram_connection() -> bool:
    """Тестовая функция для проверки подключения"""
    service = get_telegram_service()
    return await service.test_connection()