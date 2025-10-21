"""
Task Scheduler
Планировщик задач (дневные отчёты, риск-проверки, и т.д.)
"""

import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class TaskScheduler:
    """
    Планировщик периодических задач
    """
    
    def __init__(self):
        self._tasks = []
        self._running = False
    
    async def start(self):
        """Запустить планировщик"""
        if self._running:
            logger.warning("Scheduler already running")
            return
        
        self._running = True
        logger.info("✅ Task scheduler started")
        
        # Запустить задачу дневного отчёта
        daily_report_task = asyncio.create_task(self._daily_report_loop())
        self._tasks.append(daily_report_task)
        
        # Запустить задачу проверки daily reset для рисков
        daily_reset_task = asyncio.create_task(self._daily_risk_reset_loop())
        self._tasks.append(daily_reset_task)
    
    async def stop(self):
        """Остановить планировщик"""
        if not self._running:
            return
        
        self._running = False
        
        # Отменить все задачи
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        self._tasks.clear()
        logger.info("✅ Task scheduler stopped")
    
    # ═══════════════════════════════════════════════════════════
    # DAILY REPORT TASK
    # ═══════════════════════════════════════════════════════════
    
    async def _daily_report_loop(self):
        """
        Цикл отправки дневных отчётов в 00:00 UTC
        """
        logger.info("📊 Daily report task started")
        
        try:
            while self._running:
                # Вычислить время до следующей полуночи UTC
                now = datetime.now(timezone.utc)
                next_midnight = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                
                seconds_until_midnight = (next_midnight - now).total_seconds()
                
                logger.info(
                    f"📊 Next daily report in {seconds_until_midnight/3600:.1f} hours "
                    f"(at {next_midnight.strftime('%Y-%m-%d %H:%M:%S UTC')})"
                )
                
                # Ждать до полуночи
                await asyncio.sleep(seconds_until_midnight)
                
                if not self._running:
                    break
                
                # Отправить отчёт
                logger.info("📊 Sending daily report...")
                await self._send_daily_report()
        
        except asyncio.CancelledError:
            logger.info("📊 Daily report task cancelled")
        except Exception as e:
            logger.error(f"Error in daily report loop: {e}")
    
    async def _send_daily_report(self):
        """
        Отправить дневной отчёт
        """
        try:
            from app.services.daily_report import generate_and_send_daily_report
            from app.db.session import SessionLocal
            
            db = SessionLocal()
            try:
                success = await generate_and_send_daily_report(db)
                if success:
                    logger.info("✅ Daily report sent")
                else:
                    logger.error("❌ Failed to send daily report")
            finally:
                db.close()
        
        except Exception as e:
            logger.error(f"Error sending daily report: {e}")
    
    # ═══════════════════════════════════════════════════════════
    # DAILY RISK RESET TASK
    # ═══════════════════════════════════════════════════════════
    
    async def _daily_risk_reset_loop(self):
        """
        Цикл проверки необходимости daily reset для риск-менеджера
        Проверяет каждые 5 минут
        """
        logger.info("🛡️ Daily risk reset task started")
        
        try:
            while self._running:
                # Проверять каждые 5 минут
                await asyncio.sleep(300)
                
                if not self._running:
                    break
                
                # Проверить нужен ли reset
                try:
                    from app.strategy.risk import get_risk_manager
                    risk_manager = get_risk_manager()
                    
                    if risk_manager.state.should_reset_daily():
                        logger.info("🛡️ Performing daily risk reset...")
                        risk_manager.state.reset_daily()
                        logger.info("✅ Daily risk reset completed")
                
                except Exception as e:
                    logger.error(f"Error in daily risk reset: {e}")
        
        except asyncio.CancelledError:
            logger.info("🛡️ Daily risk reset task cancelled")
        except Exception as e:
            logger.error(f"Error in daily risk reset loop: {e}")


# ═══════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════

_scheduler: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    """Получить глобальный экземпляр планировщика"""
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler


async def start_scheduler():
    """Запустить планировщик"""
    scheduler = get_scheduler()
    await scheduler.start()


async def stop_scheduler():
    """Остановить планировщик"""
    scheduler = get_scheduler()
    await scheduler.stop()