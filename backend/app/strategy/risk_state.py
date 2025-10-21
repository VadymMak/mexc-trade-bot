"""
Risk State Management
Хранение текущего состояния рисков в памяти
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Tuple
from collections import deque
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    """
    Текущее состояние риск-менеджмента
    Хранит все счётчики, флаги и временные метки
    """
    
    # ===== DAILY TRACKING =====
    daily_pnl_usd: float = 0.0
    daily_trades_count: int = 0
    daily_wins: int = 0
    daily_losses: int = 0
    last_reset_date: Optional[datetime] = None
    
    # ===== SYMBOL LOSS TRACKING =====
    # {symbol: consecutive_loss_count}
    symbol_loss_streaks: Dict[str, int] = field(default_factory=dict)
    
    # {symbol: last_loss_timestamp} - для отслеживания "consecutive"
    symbol_last_loss_time: Dict[str, datetime] = field(default_factory=dict)
    
    # ===== COOLDOWNS =====
    # {symbol: cooldown_until_timestamp}
    symbol_cooldowns: Dict[str, datetime] = field(default_factory=dict)
    
    # ===== HALT STATE =====
    trading_halted: bool = False
    halt_reason: Optional[str] = None
    halted_at: Optional[datetime] = None
    
    # ===== VELOCITY TRACKING =====
    # Deque с timestamps для подсчёта трейдов за период
    trades_last_hour: deque = field(default_factory=lambda: deque(maxlen=200))
    trades_last_minute: deque = field(default_factory=lambda: deque(maxlen=50))
    
    # ===== ERROR TRACKING =====
    # Deque с timestamps системных ошибок
    recent_errors: deque = field(default_factory=lambda: deque(maxlen=50))
    consecutive_errors: int = 0
    last_error_time: Optional[datetime] = None
    
    # ===== POSITION TRACKING =====
    current_position_count: int = 0
    total_exposure_usd: float = 0.0
    
    def __post_init__(self):
        """Инициализация после создания"""
        if self.last_reset_date is None:
            self.last_reset_date = datetime.now(timezone.utc).date()
    
    # ===== DAILY TRACKING METHODS =====
    
    def add_trade_result(self, symbol: str, pnl_usd: float):
        """
        Добавить результат трейда
        """
        self.daily_pnl_usd += pnl_usd
        self.daily_trades_count += 1
        
        if pnl_usd > 0:
            self.daily_wins += 1
        elif pnl_usd < 0:
            self.daily_losses += 1
        
        # Обновить streak для символа
        self._update_symbol_streak(symbol, pnl_usd)
    
    def _update_symbol_streak(self, symbol: str, pnl_usd: float):
        """
        Обновить streak последовательных убытков для символа
        """
        now = datetime.now(timezone.utc)
        
        if pnl_usd < 0:
            # Убыток
            last_loss_time = self.symbol_last_loss_time.get(symbol)
            
            # Проверяем, был ли предыдущий убыток недавно (< 5 минут = consecutive)
            if last_loss_time and (now - last_loss_time).total_seconds() < 300:
                # Consecutive loss
                self.symbol_loss_streaks[symbol] = self.symbol_loss_streaks.get(symbol, 0) + 1
            else:
                # Первый убыток или после перерыва
                self.symbol_loss_streaks[symbol] = 1
            
            self.symbol_last_loss_time[symbol] = now
            
        else:
            # Прибыль - сбрасываем streak
            self.symbol_loss_streaks[symbol] = 0
            if symbol in self.symbol_last_loss_time:
                del self.symbol_last_loss_time[symbol]
    
    def get_symbol_loss_streak(self, symbol: str) -> int:
        """Получить текущий streak убытков для символа"""
        return self.symbol_loss_streaks.get(symbol, 0)
    
    def reset_symbol_streak(self, symbol: str):
        """Сбросить streak для символа"""
        self.symbol_loss_streaks[symbol] = 0
        if symbol in self.symbol_last_loss_time:
            del self.symbol_last_loss_time[symbol]
    
    # ===== COOLDOWN METHODS =====
    
    def add_cooldown(self, symbol: str, minutes: int):
        """
        Добавить cooldown для символа
        """
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        self.symbol_cooldowns[symbol] = cooldown_until
        logger.warning(f"Cooldown added for {symbol} until {cooldown_until.strftime('%H:%M:%S UTC')}")
    
    def is_symbol_on_cooldown(self, symbol: str) -> bool:
        """
        Проверить находится ли символ на cooldown
        Автоматически удаляет истёкшие cooldown'ы
        """
        if symbol not in self.symbol_cooldowns:
            return False
        
        cooldown_until = self.symbol_cooldowns[symbol]
        now = datetime.now(timezone.utc)
        
        if now >= cooldown_until:
            # Cooldown истёк - удаляем
            del self.symbol_cooldowns[symbol]
            logger.info(f"Cooldown expired for {symbol}")
            return False
        
        return True
    
    def get_cooldown_remaining_seconds(self, symbol: str) -> int:
        """Получить оставшееся время cooldown в секундах"""
        if symbol not in self.symbol_cooldowns:
            return 0
        
        cooldown_until = self.symbol_cooldowns[symbol]
        now = datetime.now(timezone.utc)
        remaining = (cooldown_until - now).total_seconds()
        
        return max(0, int(remaining))
    
    def clear_cooldown(self, symbol: str):
        """Удалить cooldown для символа"""
        if symbol in self.symbol_cooldowns:
            del self.symbol_cooldowns[symbol]
            logger.info(f"Cooldown cleared for {symbol}")
    
    def get_active_cooldowns(self) -> List[Tuple[str, datetime]]:
        """
        Получить список активных cooldown'ов
        Returns: [(symbol, cooldown_until), ...]
        """
        now = datetime.now(timezone.utc)
        active = []
        
        # Очистить истёкшие
        expired = [sym for sym, until in self.symbol_cooldowns.items() if now >= until]
        for sym in expired:
            del self.symbol_cooldowns[sym]
        
        # Вернуть активные
        for symbol, until in self.symbol_cooldowns.items():
            active.append((symbol, until))
        
        return sorted(active, key=lambda x: x[1])  # Сортировка по времени
    
    # ===== HALT METHODS =====
    
    def halt_trading(self, reason: str):
        """
        Остановить торговлю
        """
        if not self.trading_halted:
            self.trading_halted = True
            self.halt_reason = reason
            self.halted_at = datetime.now(timezone.utc)
            logger.critical(f"🚨 TRADING HALTED: {reason}")
    
    def resume_trading(self):
        """
        Возобновить торговлю
        """
        if self.trading_halted:
            self.trading_halted = False
            halt_duration = None
            if self.halted_at:
                halt_duration = (datetime.now(timezone.utc) - self.halted_at).total_seconds()
            self.halt_reason = None
            self.halted_at = None
            logger.info(f"✅ Trading resumed (was halted for {halt_duration:.0f}s)")
    
    def is_trading_allowed(self) -> bool:
        """Разрешена ли торговля"""
        return not self.trading_halted
    
    # ===== VELOCITY METHODS =====
    
    def track_trade_velocity(self):
        """
        Добавить timestamp текущего трейда для velocity tracking
        """
        now = datetime.now(timezone.utc)
        self.trades_last_hour.append(now)
        self.trades_last_minute.append(now)
        
        # Очистка старых (deque.maxlen делает это автоматически, но явно очистим)
        self._cleanup_velocity_tracking()
    
    def _cleanup_velocity_tracking(self):
        """Удалить старые timestamps из velocity tracking"""
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        minute_ago = now - timedelta(minutes=1)
        
        # Очистка hour deque
        while self.trades_last_hour and self.trades_last_hour[0] < hour_ago:
            self.trades_last_hour.popleft()
        
        # Очистка minute deque
        while self.trades_last_minute and self.trades_last_minute[0] < minute_ago:
            self.trades_last_minute.popleft()
    
    def get_trades_last_hour(self) -> int:
        """Получить количество трейдов за последний час"""
        self._cleanup_velocity_tracking()
        return len(self.trades_last_hour)
    
    def get_trades_last_minute(self) -> int:
        """Получить количество трейдов за последнюю минуту"""
        self._cleanup_velocity_tracking()
        return len(self.trades_last_minute)
    
    # ===== ERROR TRACKING =====
    
    def track_error(self):
        """
        Зарегистрировать системную ошибку
        """
        now = datetime.now(timezone.utc)
        self.recent_errors.append(now)
        
        # Проверяем consecutive errors (ошибки с интервалом < 10 секунд)
        if self.last_error_time and (now - self.last_error_time).total_seconds() < 10:
            self.consecutive_errors += 1
        else:
            self.consecutive_errors = 1
        
        self.last_error_time = now
    
    def get_errors_in_window(self, window_minutes: int) -> int:
        """
        Получить количество ошибок за последние N минут
        """
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(minutes=window_minutes)
        
        # Очистить старые
        while self.recent_errors and self.recent_errors[0] < threshold:
            self.recent_errors.popleft()
        
        return len(self.recent_errors)
    
    def reset_error_tracking(self):
        """Сбросить счётчики ошибок"""
        self.consecutive_errors = 0
        self.last_error_time = None
    
    # ===== POSITION TRACKING =====
    
    def update_position_count(self, count: int):
        """Обновить количество открытых позиций"""
        self.current_position_count = count
    
    def update_total_exposure(self, exposure_usd: float):
        """Обновить общую экспозицию"""
        self.total_exposure_usd = exposure_usd
    
    # ===== RESET =====
    
    def reset_daily(self):
        """
        Сбросить дневные счётчики (вызывать в полночь UTC)
        """
        today = datetime.now(timezone.utc).date()
        
        logger.info(f"📊 Daily reset: P&L=${self.daily_pnl_usd:.2f}, Trades={self.daily_trades_count}, WR={self.get_win_rate():.1f}%")
        
        self.daily_pnl_usd = 0.0
        self.daily_trades_count = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self.last_reset_date = today
        
        # Авто-resume если halt был из-за daily loss
        if self.trading_halted and self.halt_reason == "daily_loss_limit":
            self.resume_trading()
            logger.info("✅ Auto-resumed trading (new day)")
    
    def should_reset_daily(self) -> bool:
        """
        Проверить нужен ли daily reset (новый день начался)
        """
        today = datetime.now(timezone.utc).date()
        return self.last_reset_date != today
    
    # ===== STATS =====
    
    def get_win_rate(self) -> float:
        """Получить винрейт за день в процентах"""
        if self.daily_trades_count == 0:
            return 0.0
        return (self.daily_wins / self.daily_trades_count) * 100.0
    
    def get_daily_loss_pct(self, account_balance: float) -> float:
        """Получить % дневного убытка от депозита"""
        if account_balance <= 0:
            return 0.0
        return (self.daily_pnl_usd / account_balance) * 100.0
    
    def to_dict(self) -> dict:
        """
        Сериализация состояния в dict (для API)
        """
        return {
            "daily_pnl_usd": round(self.daily_pnl_usd, 2),
            "daily_trades": self.daily_trades_count,
            "daily_wins": self.daily_wins,
            "daily_losses": self.daily_losses,
            "win_rate_pct": round(self.get_win_rate(), 2),
            "trading_halted": self.trading_halted,
            "halt_reason": self.halt_reason,
            "halted_at": self.halted_at.isoformat() if self.halted_at else None,
            "active_cooldowns": [
                {
                    "symbol": sym,
                    "until": until.isoformat(),
                    "remaining_sec": self.get_cooldown_remaining_seconds(sym)
                }
                for sym, until in self.get_active_cooldowns()
            ],
            "trades_last_hour": self.get_trades_last_hour(),
            "trades_last_minute": self.get_trades_last_minute(),
            "current_positions": self.current_position_count,
            "total_exposure_usd": round(self.total_exposure_usd, 2),
            "consecutive_errors": self.consecutive_errors,
        }