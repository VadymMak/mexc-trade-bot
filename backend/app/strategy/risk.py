"""
Risk Manager
Управление рисками: лимиты, cooldowns, halt, market conditions
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, time as dt_time, timezone
from typing import Optional, Tuple, List

from app.config.risk_settings import RiskSettings, get_risk_settings
from app.strategy.risk_state import RiskState

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Централизованный менеджер рисков
    
    Отвечает за:
    - Трекинг дневных убытков и лимитов
    - Управление cooldowns по символам
    - Halt/Resume торговли
    - Проверки перед входом в позицию
    - Мониторинг velocity и ошибок
    """
    
    def __init__(self, settings: Optional[RiskSettings] = None):
        self.settings = settings or get_risk_settings()
        self.state = RiskState()
        self._lock = asyncio.Lock()

        # ── Circuit Breaker state (требует ручного рестарта) ──────────────
        self._circuit_breaker_active: bool = False
        self._cb_reason: Optional[str] = None
        self._cb_triggered_at: Optional[datetime] = None
        self._consecutive_api_errors: int = 0
        self._window_losses: deque = deque()   # (pnl_usd, ts_float)
        self._order_timestamps: deque = deque(maxlen=300)  # ts для velocity CB

        # Инициализация
        logger.info(
            f"RiskManager initialized: "
            f"balance=${self.settings.account_balance_usd}, "
            f"daily_loss_limit={self.settings.daily_loss_limit_pct}% "
            f"(${self.settings.get_daily_loss_limit_usd():.2f}), "
            f"max_positions={self.settings.get_max_positions()}"
        )
    
    # ═══════════════════════════════════════════════════════════
    # TRACK TRADE RESULTS
    # ═══════════════════════════════════════════════════════════
    
    async def track_trade_result(
        self,
        symbol: str,
        pnl_usd: float,
        qty: float = 0.0,
        price: float = 0.0
    ) -> None:
        """
        Отследить результат закрытого трейда
        
        Args:
            symbol: Символ (BTCUSDT)
            pnl_usd: Прибыль/убыток в USD
            qty: Количество (опционально)
            price: Цена (опционально)
        """
        async with self._lock:
            if self.state.should_reset_daily():
                self.state.reset_daily()

            self.state.add_trade_result(symbol, pnl_usd)
            self.state.track_trade_velocity()

            # ── Window-loss Circuit Breaker check ─────────────────────────
            if pnl_usd < 0 and self.settings.circuit_breaker_enabled:
                now_ts = datetime.now(timezone.utc).timestamp()
                self._window_losses.append((pnl_usd, now_ts))
                window_sec = self.settings.circuit_breaker_loss_window_min * 60
                while self._window_losses and (now_ts - self._window_losses[0][1]) > window_sec:
                    self._window_losses.popleft()
                window_total = sum(p for p, _ in self._window_losses)
                max_loss = self.settings.circuit_breaker_max_loss_window_usd
                if window_total <= -max_loss and not self._circuit_breaker_active:
                    await self._trigger_circuit_breaker(
                        f"window_loss:${window_total:.2f} за "
                        f"{self.settings.circuit_breaker_loss_window_min}мин"
                    )

            logger.info(
                f"Trade result: {symbol} PnL=${pnl_usd:+.2f} | "
                f"Daily: ${self.state.daily_pnl_usd:+.2f} / "
                f"${self.settings.get_daily_loss_limit_usd():.2f} | "
                f"Loss streak: {self.state.get_symbol_loss_streak(symbol)}"
            )

            await self._check_limits_after_trade(symbol, pnl_usd)
    
    async def _check_limits_after_trade(self, symbol: str, pnl_usd: float) -> None:
        """
        Проверить все лимиты после трейда
        """
        # 1. Проверка дневного лимита убытков
        if await self._check_daily_loss_limit():
            return  # halt сработал
        
        # 2. Проверка последовательных убытков по символу
        if pnl_usd < 0:
            await self._check_symbol_loss_streak(symbol)
    
    async def _check_daily_loss_limit(self) -> bool:
        """
        Проверить дневной лимит убытков
        Returns: True если halt сработал
        """
        daily_loss_limit_usd = self.settings.get_daily_loss_limit_usd()
        
        if self.state.daily_pnl_usd <= -daily_loss_limit_usd:
            logger.critical(
                f"🚨 DAILY LOSS LIMIT REACHED: "
                f"${self.state.daily_pnl_usd:.2f} <= -${daily_loss_limit_usd:.2f} "
                f"({self.settings.daily_loss_limit_pct}% of ${self.settings.account_balance_usd})"
            )

            if self.settings.circuit_breaker_enabled:
                await self._trigger_circuit_breaker(
                    f"daily_drawdown:${self.state.daily_pnl_usd:.2f}"
                )
            else:
                self.state.halt_trading("daily_loss_limit")

            try:
                from app.services.alerts import alert_daily_loss_limit
                asyncio.create_task(
                    alert_daily_loss_limit(self.state.daily_pnl_usd, daily_loss_limit_usd)
                )
            except ImportError:
                pass

            return True
        
        return False
    
    async def _check_symbol_loss_streak(self, symbol: str) -> None:
        """
        Проверить последовательные убытки по символу
        """
        streak = self.state.get_symbol_loss_streak(symbol)
        max_losses = self.settings.symbol_max_losses
        
        if streak >= max_losses:
            # Добавить cooldown
            self.state.add_cooldown(symbol, self.settings.symbol_cooldown_minutes)
            
            logger.warning(
                f"⚠️ SYMBOL COOLDOWN: {symbol} after {streak} consecutive losses | "
                f"Cooldown: {self.settings.symbol_cooldown_minutes} minutes"
            )
            
            # Алерт (будет добавлен позже)
            try:
                from app.services.alerts import alert_symbol_cooldown
                await alert_symbol_cooldown(symbol, self.settings.symbol_cooldown_minutes)
            except ImportError:
                pass
    
    # ═══════════════════════════════════════════════════════════
    # POSITION CHECKS (перед входом)
    # ═══════════════════════════════════════════════════════════
    
    async def can_open_position(
        self,
        symbol: str,
        size_usd: float
    ) -> Tuple[bool, str]:
        """
        Проверить можно ли открыть позицию
        
        Args:
            symbol: Символ
            size_usd: Размер позиции в USD
            
        Returns:
            (can_open, reason)
            - (True, "OK") если можно
            - (False, "reason") если нельзя
        """
        async with self._lock:
            # 1. Проверка halt
            if self.state.trading_halted:
                return False, f"Trading halted: {self.state.halt_reason}"
            
            # 2. Проверка cooldown
            if self.state.is_symbol_on_cooldown(symbol):
                remaining = self.state.get_cooldown_remaining_seconds(symbol)
                return False, f"Symbol on cooldown ({remaining}s remaining)"
            
            # 3. Проверка trading hours
            if not self._is_trading_hours():
                return False, "Outside trading hours"
            
            # 4. Проверка max positions
            max_positions = self.settings.get_max_positions()
            if self.state.current_position_count >= max_positions:
                return False, f"Max positions reached ({max_positions})"
            
            # 5. Проверка размера позиции
            max_position_size = self.settings.get_max_position_size_usd()
            if size_usd > max_position_size:
                return False, f"Position too large (${size_usd:.2f} > ${max_position_size:.2f})"
            
            # 6. Проверка velocity
            if not self._is_velocity_ok():
                trades_hour = self.state.get_trades_last_hour()
                trades_min = self.state.get_trades_last_minute()
                return False, f"Velocity limit (hour:{trades_hour}, min:{trades_min})"
            
            return True, "OK"
    
    def _is_trading_hours(self) -> bool:
        """
        Проверить находимся ли в торговых часах
        """
        if not self.settings.trading_hours_enabled:
            return True
        
        now = datetime.now(timezone.utc).time()
        
        # Парсинг времени
        try:
            start_str = self.settings.trading_hours_start
            end_str = self.settings.trading_hours_end
            
            start_h, start_m = map(int, start_str.split(':'))
            end_h, end_m = map(int, end_str.split(':'))
            
            start_time = dt_time(start_h, start_m)
            end_time = dt_time(end_h, end_m)
            
            # Проверка диапазона
            if start_time <= end_time:
                # Обычный диапазон (например, 08:00-22:00)
                return start_time <= now <= end_time
            else:
                # Диапазон через полночь (например, 22:00-08:00)
                return now >= start_time or now <= end_time
        
        except Exception as e:
            logger.error(f"Error parsing trading hours: {e}")
            return True  # При ошибке разрешаем торговлю
    
    def _is_velocity_ok(self) -> bool:
        """
        Проверить не превышены ли лимиты скорости торговли
        """
        trades_hour = self.state.get_trades_last_hour()
        trades_min = self.state.get_trades_last_minute()
        
        if trades_hour >= self.settings.max_trades_per_hour:
            return False
        
        if trades_min >= self.settings.max_trades_per_minute:
            return False
        
        return True
    
    # ═══════════════════════════════════════════════════════════
    # HALT / RESUME
    # ═══════════════════════════════════════════════════════════
    
    async def halt_trading(self, reason: str) -> None:
        """
        Остановить торговлю
        """
        async with self._lock:
            self.state.halt_trading(reason)
    
    async def resume_trading(self) -> None:
        """Возобновить торговлю. Блокировано если активен Circuit Breaker."""
        async with self._lock:
            if self._circuit_breaker_active:
                logger.error(
                    "❌ resume_trading blocked: Circuit Breaker активен. "
                    "Вызовите manual_reset_circuit_breaker() для ручного сброса."
                )
                return
            self.state.resume_trading()
    
    def is_trading_allowed(self) -> bool:
        """
        Разрешена ли торговля
        """
        return self.state.is_trading_allowed() and self._is_trading_hours()
    
    # 👇 ADD THE NEW METHOD HERE 👇
    def can_trade(self) -> bool:
        """
        Alias for is_trading_allowed() - used by strategy engine
        Checks if trading is globally allowed
        """
        return self.is_trading_allowed()
    
    # ═══════════════════════════════════════════════════════════
    # COOLDOWN MANAGEMENT
    # ═══════════════════════════════════════════════════════════
    
    def is_symbol_on_cooldown(self, symbol: str) -> bool:
        """
        Check if symbol is on cooldown
        (Proxy method for engine.py compatibility)
        """
        return self.state.is_symbol_on_cooldown(symbol)
    
    async def clear_cooldown(self, symbol: str) -> None:
        """
        Очистить cooldown для символа
        """
        async with self._lock:
            self.state.clear_cooldown(symbol)
    
    def get_active_cooldowns(self) -> List[Tuple[str, datetime]]:
        """
        Получить список активных cooldown'ов
        """
        return self.state.get_active_cooldowns()
    
    # ═══════════════════════════════════════════════════════════
    # ERROR TRACKING
    # ═══════════════════════════════════════════════════════════
    
    async def track_error(self, error_type: str = "system") -> None:
        """
        Зарегистрировать системную ошибку
        """
        async with self._lock:
            self.state.track_error()
            
            # Проверить порог ошибок
            errors_in_window = self.state.get_errors_in_window(
                self.settings.error_window_minutes
            )
            
            if errors_in_window >= self.settings.max_consecutive_errors:
                self.state.halt_trading("excessive_errors")
                
                logger.critical(
                    f"🚨 EXCESSIVE ERRORS: {errors_in_window} errors in "
                    f"{self.settings.error_window_minutes} minutes | Trading halted"
                )
                
                # Алерт (будет добавлен позже)
                try:
                    from app.services.alerts import alert_system_error
                    await alert_system_error(
                        "excessive_errors",
                        f"{errors_in_window} errors in {self.settings.error_window_minutes}min",
                        ""
                    )
                except ImportError:
                    pass
    
    # ═══════════════════════════════════════════════════════════
    # CIRCUIT BREAKER
    # ═══════════════════════════════════════════════════════════

    async def _trigger_circuit_breaker(self, reason: str, executor=None) -> None:
        """
        Трипает Circuit Breaker:
          1. Хол всей торговли (halt_trading)
          2. Закрытие всех позиций через executor (если передан)
          3. Критический лог
          4. Только ручной рестарт (manual_reset_circuit_breaker)

        Безопасен для вызова из-под asyncio.Lock — не ждёт IO, планирует через create_task.
        """
        if self._circuit_breaker_active:
            return

        self._circuit_breaker_active = True
        self._cb_reason = reason
        self._cb_triggered_at = datetime.now(timezone.utc)

        self.state.halt_trading(f"circuit_breaker:{reason}")

        logger.critical(
            f"🔴 CIRCUIT BREAKER TRIPPED: {reason} | "
            f"Торговля остановлена. Только ручной рестарт."
        )

        # Закрыть позиции асинхронно (чтобы не дедлочить lock)
        _exec = executor
        async def _close_all():
            closed = 0
            if _exec and hasattr(_exec, 'get_all_positions'):
                try:
                    positions = await _exec.get_all_positions()
                    for pos in (positions or []):
                        sym = pos.get('symbol')
                        if sym:
                            try:
                                await _exec.flatten_symbol(sym)
                                closed += 1
                            except Exception as e:
                                logger.error(f"CB close {sym}: {e}")
                except Exception as e:
                    logger.error(f"CB get_positions: {e}")
            logger.critical(f"🔴 Circuit Breaker: закрыто {closed} позиций")

        asyncio.create_task(_close_all())

        try:
            from app.services.alerts import alert_circuit_breaker
            asyncio.create_task(alert_circuit_breaker(reason, 0))
        except (ImportError, Exception):
            pass

    async def track_ws_lag(self, lag_ms: float, executor=None) -> None:
        """Трекинг WS лага. Если > порога — трипает CB."""
        if not self.settings.circuit_breaker_enabled:
            return
        threshold = self.settings.circuit_breaker_ws_lag_threshold_ms
        if lag_ms > threshold and not self._circuit_breaker_active:
            async with self._lock:
                if not self._circuit_breaker_active:
                    await self._trigger_circuit_breaker(
                        f"ws_lag:{lag_ms:.0f}ms > {threshold}ms", executor
                    )

    async def track_api_error(self, error_type: str = "api", executor=None) -> None:
        """
        Трекинг ошибок API. При >= circuit_breaker_max_api_errors → CB.
        Дополняет существующий track_error() для системных ошибок.
        """
        async with self._lock:
            self._consecutive_api_errors += 1
            threshold = self.settings.circuit_breaker_max_api_errors
            if (
                self.settings.circuit_breaker_enabled
                and self._consecutive_api_errors >= threshold
                and not self._circuit_breaker_active
            ):
                await self._trigger_circuit_breaker(
                    f"api_errors:{self._consecutive_api_errors}_подряд"
                )
        # Также делегируем в существующий track_error
        await self.track_error(error_type)

    def clear_api_errors(self) -> None:
        """Сбросить счётчик подряд-ошибок API (вызывать при успешном запросе)."""
        self._consecutive_api_errors = 0

    async def track_order(self, executor=None) -> None:
        """
        Трекинг размещённых ордеров для velocity CB.
        Триггер: ордеров/мин > max_trades_per_minute × 3
        """
        if not self.settings.circuit_breaker_enabled:
            return
        async with self._lock:
            now_ts = datetime.now(timezone.utc).timestamp()
            self._order_timestamps.append(now_ts)
            # Подсчёт за последнюю минуту
            cutoff = now_ts - 60.0
            recent = sum(1 for ts in self._order_timestamps if ts > cutoff)
            threshold = self.settings.max_trades_per_minute * 3
            if recent > threshold and not self._circuit_breaker_active:
                await self._trigger_circuit_breaker(
                    f"order_rate:{recent}/мин > {threshold}", executor
                )

    def is_circuit_breaker_active(self) -> bool:
        """Активен ли Circuit Breaker."""
        return self._circuit_breaker_active

    async def manual_reset_circuit_breaker(self, operator: str = "manual") -> None:
        """
        Ручной сброс Circuit Breaker.
        Единственный способ возобновить торговлю после CB.
        """
        async with self._lock:
            if not self._circuit_breaker_active:
                logger.info("Circuit Breaker не активен — сбрасывать нечего")
                return

            self._circuit_breaker_active = False
            prev_reason = self._cb_reason
            self._cb_reason = None
            self._cb_triggered_at = None
            self._consecutive_api_errors = 0
            self._window_losses.clear()
            self._order_timestamps.clear()

            self.state.resume_trading()

            logger.critical(
                f"✅ CIRCUIT BREAKER СБРОШЕН оператором '{operator}' "
                f"(был: {prev_reason})"
            )

    # ═══════════════════════════════════════════════════════════
    # EMERGENCY STOP
    # ═══════════════════════════════════════════════════════════
    
    async def emergency_stop(self, executor=None) -> int:
        """
        Аварийная остановка всей торговли
        
        Args:
            executor: Execution port (для закрытия позиций)
            
        Returns:
            Количество закрытых позиций
        """
        async with self._lock:
            self.state.halt_trading("emergency_stop")
            
            logger.critical("🚨 EMERGENCY STOP ACTIVATED")
            
            closed_count = 0
            
            # Закрыть все позиции через executor (если предоставлен)
            if executor and hasattr(executor, 'get_all_positions'):
                try:
                    positions = await executor.get_all_positions()
                    for pos in positions:
                        symbol = pos.get('symbol')
                        if symbol:
                            try:
                                await executor.flatten_symbol(symbol)
                                closed_count += 1
                            except Exception as e:
                                logger.error(f"Failed to flatten {symbol}: {e}")
                except Exception as e:
                    logger.error(f"Failed to get positions during emergency stop: {e}")
            
            # Алерт (будет добавлен позже)
            try:
                from app.services.alerts import alert_emergency_stop
                await alert_emergency_stop(closed_count)
            except ImportError:
                pass
    
    # ═══════════════════════════════════════════════════════════
    # POSITION UPDATES
    # ═══════════════════════════════════════════════════════════
    
    async def update_position_count(self, count: int) -> None:
        """
        Обновить количество открытых позиций
        """
        async with self._lock:
            self.state.update_position_count(count)
    
    async def update_total_exposure(self, exposure_usd: float) -> None:
        """
        Обновить общую экспозицию
        """
        async with self._lock:
            self.state.update_total_exposure(exposure_usd)
    
    # ═══════════════════════════════════════════════════════════
    # SETTINGS UPDATE
    # ═══════════════════════════════════════════════════════════
    
    def update_balance(self, new_balance_usd: float) -> None:
        """
        Обновить баланс депозита (пересчитает все лимиты)
        """
        self.settings.update_balance(new_balance_usd)
        logger.info(
            f"Balance updated: ${new_balance_usd:.2f} | "
            f"New daily loss limit: ${self.settings.get_daily_loss_limit_usd():.2f}"
        )
    
    # ═══════════════════════════════════════════════════════════
    # STATUS / DIAGNOSTICS
    # ═══════════════════════════════════════════════════════════
    
    def get_status(self) -> dict:
        """
        Получить текущий статус рисков (для API)
        """
        return {
            "trading_allowed": self.is_trading_allowed(),
            "trading_halted": self.state.trading_halted,
            "halt_reason": self.state.halt_reason,
            "halted_at": self.state.halted_at.isoformat() if self.state.halted_at else None,

            # Circuit Breaker
            "circuit_breaker_active": self._circuit_breaker_active,
            "circuit_breaker_reason": self._cb_reason,
            "circuit_breaker_triggered_at": (
                self._cb_triggered_at.isoformat() if self._cb_triggered_at else None
            ),
            "manual_restart_required": self._circuit_breaker_active,
            "consecutive_api_errors": self._consecutive_api_errors,
            
            # Daily stats
            "daily_pnl_usd": round(self.state.daily_pnl_usd, 2),
            "daily_loss_limit_usd": round(self.settings.get_daily_loss_limit_usd(), 2),
            "daily_loss_pct": round(self.state.get_daily_loss_pct(self.settings.account_balance_usd), 2),
            "daily_trades": self.state.daily_trades_count,
            "win_rate_pct": round(self.state.get_win_rate(), 2),
            
            # Position limits
            "max_positions": self.settings.get_max_positions(),
            "current_positions": self.state.current_position_count,
            "max_position_size_usd": round(self.settings.get_max_position_size_usd(), 2),
            "total_exposure_usd": round(self.state.total_exposure_usd, 2),
            
            # Cooldowns
            "active_cooldowns": [
                {
                    "symbol": sym,
                    "until": until.isoformat(),
                    "remaining_sec": self.state.get_cooldown_remaining_seconds(sym)
                }
                for sym, until in self.state.get_active_cooldowns()
            ],
            
            # Velocity
            "trades_last_hour": self.state.get_trades_last_hour(),
            "trades_last_minute": self.state.get_trades_last_minute(),
            "max_trades_per_hour": self.settings.max_trades_per_hour,
            "max_trades_per_minute": self.settings.max_trades_per_minute,
            
            # Errors
            "consecutive_errors": self.state.consecutive_errors,
            "errors_in_window": self.state.get_errors_in_window(self.settings.error_window_minutes),
            
            # Settings
            "account_balance_usd": self.settings.account_balance_usd,
            "trading_hours_enabled": self.settings.trading_hours_enabled,
            "is_trading_hours": self._is_trading_hours(),
        }
    
    def get_limits(self) -> dict:
        """
        Получить все лимиты (для API)
        """
        return {
            # Account
            "account_balance_usd": self.settings.account_balance_usd,
            
            # Daily limits
            "daily_loss_limit_pct": self.settings.daily_loss_limit_pct,
            "daily_loss_limit_usd": self.settings.get_daily_loss_limit_usd(),
            "daily_profit_target_pct": self.settings.daily_profit_target_pct,
            
            # Symbol limits
            "symbol_max_losses": self.settings.symbol_max_losses,
            "symbol_cooldown_minutes": self.settings.symbol_cooldown_minutes,
            
            # Position limits
            "max_exposure_per_position_pct": self.settings.max_exposure_per_position_pct,
            "max_position_size_usd": self.settings.get_max_position_size_usd(),
            "max_positions": self.settings.get_max_positions(),
            
            # Velocity limits
            "max_trades_per_hour": self.settings.max_trades_per_hour,
            "max_trades_per_minute": self.settings.max_trades_per_minute,
            
            # Trading hours
            "trading_hours_enabled": self.settings.trading_hours_enabled,
            "trading_hours_start": self.settings.trading_hours_start,
            "trading_hours_end": self.settings.trading_hours_end,
            
            # Market conditions
            "btc_atr_threshold_pct": self.settings.btc_atr_threshold_pct,
            "spread_widening_multiplier": self.settings.spread_widening_multiplier,
            "volume_drop_threshold_pct": self.settings.volume_drop_threshold_pct,
            
            # Error limits
            "max_consecutive_errors": self.settings.max_consecutive_errors,
            "error_window_minutes": self.settings.error_window_minutes,
        }

# ═══════════════════════════════════════════════════════════
# DYNAMIC STOP LOSS CALCULATOR
# ═══════════════════════════════════════════════════════════

def calculate_dynamic_sl(
    atr_pct: float,
    spread_bps: float,
    imbalance: float,
    base_sl_bps: float = -3.0
) -> float:
    """
    Вычисляет динамический Stop Loss на основе рыночных условий.
    
    Args:
        atr_pct: ATR в процентах (волатильность, например 0.12 = 0.12%)
        spread_bps: Текущий спред в basis points
        imbalance: Дисбаланс стакана bid/(bid+ask), 0.0-1.0
        base_sl_bps: Базовый SL в bps (отрицательное число, по умолчанию -3.0)
    
    Returns:
        Динамический SL в bps (отрицательное число)
        
    Example:
        >>> calculate_dynamic_sl(atr_pct=0.05, spread_bps=2.5, imbalance=0.5)
        -3.0  # Спокойный рынок, узкий SL
        
        >>> calculate_dynamic_sl(atr_pct=0.25, spread_bps=8.0, imbalance=0.8)
        -7.92  # Волатильный рынок, широкий SL
    """
    # ───────────────────────────────────────────────────────
    # 1. ATR ФАКТОР (волатильность)
    # ───────────────────────────────────────────────────────
    if atr_pct < 0.05:
        # Очень спокойный рынок
        atr_factor = 1.0
    elif atr_pct < 0.15:
        # Нормальная волатильность
        atr_factor = 1.3
    elif atr_pct < 0.30:
        # Активный рынок
        atr_factor = 1.6
    else:
        # Высокая волатильность
        atr_factor = 2.0
    
    # ───────────────────────────────────────────────────────
    # 2. SPREAD ФАКТОР (ликвидность)
    # ───────────────────────────────────────────────────────
    if spread_bps < 3.0:
        # Узкий спред, высокая ликвидность
        spread_factor = 1.0
    elif spread_bps < 8.0:
        # Средний спред
        spread_factor = 1.2
    else:
        # Широкий спред, низкая ликвидность
        spread_factor = 1.5
    
    # ───────────────────────────────────────────────────────
    # 3. IMBALANCE ФАКТОР (дисбаланс стакана)
    # ───────────────────────────────────────────────────────
    if 0.4 <= imbalance <= 0.6:
        # Сбалансированный стакан
        imbalance_factor = 1.0
    else:
        # Дисбаланс - нужен более широкий SL
        imbalance_factor = 1.1
    
    # ───────────────────────────────────────────────────────
    # 4. ИТОГОВЫЙ РАСЧЁТ
    # ───────────────────────────────────────────────────────
    dynamic_sl_bps = base_sl_bps * atr_factor * spread_factor * imbalance_factor
    
    # ───────────────────────────────────────────────────────
    # 5. ОГРАНИЧЕНИЯ (не слишком узкий, не слишком широкий)
    # ───────────────────────────────────────────────────────
    dynamic_sl_bps = max(dynamic_sl_bps, -10.0)  # Не шире -10 bps
    dynamic_sl_bps = min(dynamic_sl_bps, -2.0)   # Не уже -2 bps
    
    return dynamic_sl_bps

# ═══════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════

_risk_manager: Optional[RiskManager] = None


def get_risk_manager() -> RiskManager:
    """Получить глобальный экземпляр риск-менеджера (singleton)"""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager


def reload_risk_manager() -> RiskManager:
    """Перезагрузить риск-менеджер (например, после изменения настроек)"""
    global _risk_manager
    _risk_manager = RiskManager()
    return _risk_manager