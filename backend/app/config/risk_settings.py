"""
Risk Management Settings
Конфигурация лимитов и правил риск-менеджмента
"""

import os
from typing import Optional
from pydantic import BaseModel, Field, validator


class RiskSettings(BaseModel):
    """
    Настройки риск-менеджмента
    Все лимиты настраиваемы через ENV переменные
    """
    
    # ===== ACCOUNT SETTINGS =====
    account_balance_usd: float = Field(
        default=1000.0,
        description="Размер депозита в USD (для расчёта % лимитов)"
    )
    
    # ===== DAILY LOSS LIMITS =====
    daily_loss_limit_pct: float = Field(
        default=2.0,
        ge=0.1,
        le=50.0,
        description="Макс дневной убыток в % от депозита (2% = $20 при $1000)"
    )
    
    daily_profit_target_pct: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Целевая дневная прибыль в % (опционально, для алертов)"
    )
    
    # ===== SYMBOL LOSS LIMITS =====
    symbol_max_losses: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Макс последовательных убытков по символу перед cooldown"
    )
    
    symbol_cooldown_minutes: int = Field(
        default=60,
        ge=5,
        le=1440,
        description="Время cooldown после достижения лимита убытков (минуты)"
    )
    
    # ===== POSITION LIMITS =====
    max_exposure_per_position_pct: float = Field(
        default=20.0,
        ge=1.0,
        le=100.0,
        description="Макс экспозиция на одну позицию в % от депозита"
    )
    
    max_positions_fixed: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description="Фиксированное число макс позиций (если None - динамический расчёт)"
    )
    
    max_positions_dynamic_divisor: int = Field(
        default=200,
        ge=50,
        le=1000,
        description="Делитель для динамического расчёта: max_pos = balance // divisor"
    )
    
    # ===== VELOCITY LIMITS =====
    max_trades_per_hour: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Макс количество трейдов в час"
    )
    
    max_trades_per_minute: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Макс количество трейдов в минуту"
    )
    
    # ===== TRADING HOURS =====
    trading_hours_enabled: bool = Field(
        default=False,
        description="Включить ограничение торговых часов"
    )
    
    trading_hours_start: str = Field(
        default="08:00",
        description="Начало торговых часов (UTC, формат HH:MM)"
    )
    
    trading_hours_end: str = Field(
        default="22:00",
        description="Конец торговых часов (UTC, формат HH:MM)"
    )
    
    # ===== MARKET CONDITIONS =====
    btc_atr_threshold_pct: float = Field(
        default=3.0,
        ge=0.5,
        le=20.0,
        description="Порог ATR% для BTC - выше этого = высокая волатильность"
    )
    
    spread_widening_multiplier: float = Field(
        default=2.5,
        ge=1.5,
        le=10.0,
        description="Множитель нормального спреда - выше = слишком широкий"
    )
    
    volume_drop_threshold_pct: float = Field(
        default=50.0,
        ge=10.0,
        le=90.0,
        description="% падения объёма от нормального - ниже = низкая ликвидность"
    )
    
    # ===== ERROR LIMITS =====
    max_consecutive_errors: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Макс последовательных системных ошибок перед halt"
    )
    
    error_window_minutes: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Временное окно для подсчёта ошибок (минуты)"
    )
    
    class Config:
        env_prefix = "RISK_"
        case_sensitive = False
    
    @validator("trading_hours_start", "trading_hours_end")
    def validate_time_format(cls, v):
        """Валидация формата времени HH:MM"""
        try:
            hours, minutes = v.split(":")
            h, m = int(hours), int(minutes)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
            return v
        except (ValueError, AttributeError):
            raise ValueError(f"Invalid time format: {v}. Expected HH:MM")
    
    # ===== CALCULATED PROPERTIES =====
    
    def get_daily_loss_limit_usd(self) -> float:
        """Получить дневной лимит убытков в USD"""
        return self.account_balance_usd * (self.daily_loss_limit_pct / 100.0)
    
    def get_daily_profit_target_usd(self) -> Optional[float]:
        """Получить целевую прибыль в USD"""
        if self.daily_profit_target_pct is None:
            return None
        return self.account_balance_usd * (self.daily_profit_target_pct / 100.0)
    
    def get_max_positions(self) -> int:
        """Получить макс количество позиций (фиксированное или динамическое)"""
        if self.max_positions_fixed is not None:
            return self.max_positions_fixed
        
        # Динамический расчёт: balance // divisor, но не меньше 3 и не больше 10
        dynamic = max(3, min(10, int(self.account_balance_usd // self.max_positions_dynamic_divisor)))
        return dynamic
    
    def get_max_position_size_usd(self) -> float:
        """Получить макс размер одной позиции в USD"""
        return self.account_balance_usd * (self.max_exposure_per_position_pct / 100.0)
    
    def update_balance(self, new_balance_usd: float):
        """Обновить баланс депозита (пересчитает все лимиты)"""
        if new_balance_usd <= 0:
            raise ValueError(f"Balance must be positive, got {new_balance_usd}")
        self.account_balance_usd = new_balance_usd


def load_risk_settings() -> RiskSettings:
    """
    Загрузить настройки рисков из ENV переменных
    """
    return RiskSettings(
        account_balance_usd=float(os.getenv("RISK_ACCOUNT_BALANCE_USD", "1000.0")),
        daily_loss_limit_pct=float(os.getenv("RISK_DAILY_LOSS_LIMIT_PCT", "2.0")),
        daily_profit_target_pct=float(os.getenv("RISK_DAILY_PROFIT_TARGET_PCT")) if os.getenv("RISK_DAILY_PROFIT_TARGET_PCT") else None,
        symbol_max_losses=int(os.getenv("RISK_SYMBOL_MAX_LOSSES", "3")),
        symbol_cooldown_minutes=int(os.getenv("RISK_SYMBOL_COOLDOWN_MINUTES", "60")),
        max_exposure_per_position_pct=float(os.getenv("RISK_MAX_EXPOSURE_PER_POSITION_PCT", "20.0")),
        max_positions_fixed=int(os.getenv("RISK_MAX_POSITIONS_FIXED")) if os.getenv("RISK_MAX_POSITIONS_FIXED") else None,
        max_positions_dynamic_divisor=int(os.getenv("RISK_MAX_POSITIONS_DYNAMIC_DIVISOR", "200")),
        max_trades_per_hour=int(os.getenv("RISK_MAX_TRADES_PER_HOUR", "100")),
        max_trades_per_minute=int(os.getenv("RISK_MAX_TRADES_PER_MINUTE", "10")),
        trading_hours_enabled=os.getenv("RISK_TRADING_HOURS_ENABLED", "false").lower() == "true",
        trading_hours_start=os.getenv("RISK_TRADING_HOURS_START", "08:00"),
        trading_hours_end=os.getenv("RISK_TRADING_HOURS_END", "22:00"),
        btc_atr_threshold_pct=float(os.getenv("RISK_BTC_ATR_THRESHOLD_PCT", "3.0")),
        spread_widening_multiplier=float(os.getenv("RISK_SPREAD_WIDENING_MULTIPLIER", "2.5")),
        volume_drop_threshold_pct=float(os.getenv("RISK_VOLUME_DROP_THRESHOLD_PCT", "50.0")),
        max_consecutive_errors=int(os.getenv("RISK_MAX_CONSECUTIVE_ERRORS", "5")),
        error_window_minutes=int(os.getenv("RISK_ERROR_WINDOW_MINUTES", "5")),
    )


# Singleton instance
_risk_settings: Optional[RiskSettings] = None


def get_risk_settings() -> RiskSettings:
    """Получить глобальный экземпляр настроек (singleton)"""
    global _risk_settings
    if _risk_settings is None:
        _risk_settings = load_risk_settings()
    return _risk_settings


def reload_risk_settings() -> RiskSettings:
    """Перезагрузить настройки из ENV (например, после изменений)"""
    global _risk_settings
    _risk_settings = load_risk_settings()
    return _risk_settings