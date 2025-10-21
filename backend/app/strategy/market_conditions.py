"""
Market Conditions Monitor
Мониторинг рыночных условий: BTC volatility, spread widening, volume collapse
"""

from __future__ import annotations

import logging
from typing import Dict, Optional
from collections import deque
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class MarketConditionMonitor:
    """
    Монитор рыночных условий
    
    Отслеживает:
    - BTC волатильность (ATR%)
    - Расширение спредов
    - Падение объёмов
    """
    
    def __init__(self):
        # BTC volatility
        self.btc_atr_pct: float = 0.0
        self.btc_last_updated: Optional[datetime] = None
        
        # Spread tracking (EMA по символам)
        # {symbol: ema_spread_bps}
        self.symbol_normal_spreads: Dict[str, float] = {}
        
        # Volume tracking (EMA по символам)
        # {symbol: ema_usdpm}
        self.symbol_normal_volumes: Dict[str, float] = {}
        
        # EMA параметры
        self.ema_alpha = 0.05  # weight for new value (0.05 = slow EMA~20 periods)
        
        logger.info("MarketConditionMonitor initialized")
    
    # ═══════════════════════════════════════════════════════════
    # BTC VOLATILITY MONITORING
    # ═══════════════════════════════════════════════════════════
    
    def update_btc_volatility(self, atr_pct: float) -> None:
        """
        Обновить BTC волатильность (ATR%)
        
        Args:
            atr_pct: ATR в процентах (например, 2.5 = 2.5%)
        """
        self.btc_atr_pct = atr_pct
        self.btc_last_updated = datetime.now(timezone.utc)
        
        logger.debug(f"BTC volatility updated: ATR={atr_pct:.2f}%")
    
    def is_btc_volatile(self, threshold_pct: float = 3.0) -> bool:
        """
        Проверить превышает ли волатильность BTC порог
        
        Args:
            threshold_pct: Порог ATR% (default: 3.0%)
            
        Returns:
            True если BTC слишком волатилен
        """
        # Если данные устарели (> 5 минут) - считаем что нет данных
        if self.btc_last_updated:
            age = (datetime.now(timezone.utc) - self.btc_last_updated).total_seconds()
            if age > 300:  # 5 minutes
                logger.warning(f"BTC volatility data stale ({age:.0f}s old)")
                return False  # При отсутствии данных не блокируем
        
        is_volatile = self.btc_atr_pct > threshold_pct
        
        if is_volatile:
            logger.warning(
                f"⚠️ BTC HIGH VOLATILITY: ATR={self.btc_atr_pct:.2f}% > {threshold_pct:.2f}%"
            )
        
        return is_volatile
    
    def get_btc_atr_pct(self) -> float:
        """Получить текущий BTC ATR%"""
        return self.btc_atr_pct
    
    # ═══════════════════════════════════════════════════════════
    # SPREAD MONITORING
    # ═══════════════════════════════════════════════════════════
    
    def update_symbol_spread(self, symbol: str, spread_bps: float) -> None:
        """
        Обновить нормальный спред для символа (EMA)
        
        Args:
            symbol: Символ (BTCUSDT)
            spread_bps: Текущий спред в bps
        """
        if symbol not in self.symbol_normal_spreads:
            # Первое значение
            self.symbol_normal_spreads[symbol] = spread_bps
        else:
            # EMA update: ema_new = ema_old * (1 - alpha) + value * alpha
            old_ema = self.symbol_normal_spreads[symbol]
            new_ema = old_ema * (1.0 - self.ema_alpha) + spread_bps * self.ema_alpha
            self.symbol_normal_spreads[symbol] = new_ema
    
    def is_spread_normal(
        self,
        symbol: str,
        current_spread_bps: float,
        multiplier: float = 2.5
    ) -> bool:
        """
        Проверить является ли текущий спред нормальным
        
        Args:
            symbol: Символ
            current_spread_bps: Текущий спред в bps
            multiplier: Множитель нормального спреда (default: 2.5x)
            
        Returns:
            True если спред нормальный
        """
        if symbol not in self.symbol_normal_spreads:
            # Нет истории - принимаем текущий спред как норму
            self.symbol_normal_spreads[symbol] = current_spread_bps
            return True
        
        normal_spread = self.symbol_normal_spreads[symbol]
        threshold = normal_spread * multiplier
        
        is_normal = current_spread_bps <= threshold
        
        if not is_normal:
            logger.warning(
                f"⚠️ SPREAD WIDENING: {symbol} spread={current_spread_bps:.2f} bps > "
                f"{threshold:.2f} bps ({multiplier}x normal)"
            )
        
        return is_normal
    
    def get_normal_spread(self, symbol: str) -> Optional[float]:
        """Получить нормальный спред для символа"""
        return self.symbol_normal_spreads.get(symbol)
    
    # ═══════════════════════════════════════════════════════════
    # VOLUME MONITORING
    # ═══════════════════════════════════════════════════════════
    
    def update_symbol_volume(self, symbol: str, usdpm: float) -> None:
        """
        Обновить нормальный объём для символа (EMA)
        
        Args:
            symbol: Символ
            usdpm: Текущий USD per minute
        """
        if symbol not in self.symbol_normal_volumes:
            # Первое значение
            self.symbol_normal_volumes[symbol] = usdpm
        else:
            # EMA update
            old_ema = self.symbol_normal_volumes[symbol]
            new_ema = old_ema * (1.0 - self.ema_alpha) + usdpm * self.ema_alpha
            self.symbol_normal_volumes[symbol] = new_ema
    
    def is_volume_healthy(
        self,
        symbol: str,
        current_usdpm: float,
        threshold_pct: float = 50.0
    ) -> bool:
        """
        Проверить является ли текущий объём здоровым
        
        Args:
            symbol: Символ
            current_usdpm: Текущий USD/min
            threshold_pct: Мин % от нормального (default: 50%)
            
        Returns:
            True если объём здоровый
        """
        if symbol not in self.symbol_normal_volumes:
            # Нет истории - принимаем текущий объём как норму
            self.symbol_normal_volumes[symbol] = current_usdpm
            return True
        
        normal_volume = self.symbol_normal_volumes[symbol]
        threshold = normal_volume * (threshold_pct / 100.0)
        
        is_healthy = current_usdpm >= threshold
        
        if not is_healthy:
            logger.warning(
                f"⚠️ VOLUME COLLAPSE: {symbol} usdpm={current_usdpm:.2f} < "
                f"{threshold:.2f} ({threshold_pct}% of normal)"
            )
        
        return is_healthy
    
    def get_normal_volume(self, symbol: str) -> Optional[float]:
        """Получить нормальный объём для символа"""
        return self.symbol_normal_volumes.get(symbol)
    
    # ═══════════════════════════════════════════════════════════
    # COMBINED CHECK
    # ═══════════════════════════════════════════════════════════
    
    def check_market_conditions(
        self,
        symbol: str,
        spread_bps: float,
        usdpm: float,
        btc_atr_threshold: float = 3.0,
        spread_multiplier: float = 2.5,
        volume_threshold_pct: float = 50.0
    ) -> tuple[bool, list[str]]:
        """
        Комплексная проверка рыночных условий
        
        Args:
            symbol: Символ
            spread_bps: Текущий спред
            usdpm: Текущий USD/min
            btc_atr_threshold: Порог BTC ATR%
            spread_multiplier: Множитель спреда
            volume_threshold_pct: Порог объёма %
            
        Returns:
            (conditions_ok, reasons)
            - True если все условия ОК
            - False + список причин если не ОК
        """
        reasons = []
        
        # 1. BTC volatility
        if self.is_btc_volatile(btc_atr_threshold):
            reasons.append(f"btc_volatile(ATR={self.btc_atr_pct:.2f}%)")
        
        # 2. Spread widening
        if not self.is_spread_normal(symbol, spread_bps, spread_multiplier):
            reasons.append(f"spread_wide({spread_bps:.2f}bps)")
        
        # 3. Volume collapse
        if not self.is_volume_healthy(symbol, usdpm, volume_threshold_pct):
            reasons.append(f"volume_low({usdpm:.2f})")
        
        conditions_ok = len(reasons) == 0
        
        return conditions_ok, reasons
    
    # ═══════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ═══════════════════════════════════════════════════════════
    
    def get_status(self) -> dict:
        """
        Получить текущий статус мониторинга (для API)
        """
        return {
            "btc_atr_pct": round(self.btc_atr_pct, 2),
            "btc_last_updated": self.btc_last_updated.isoformat() if self.btc_last_updated else None,
            "tracked_symbols_spread": len(self.symbol_normal_spreads),
            "tracked_symbols_volume": len(self.symbol_normal_volumes),
            "ema_alpha": self.ema_alpha,
        }
    
    def get_symbol_stats(self, symbol: str) -> dict:
        """
        Получить статистику для символа
        """
        return {
            "symbol": symbol,
            "normal_spread_bps": self.symbol_normal_spreads.get(symbol),
            "normal_usdpm": self.symbol_normal_volumes.get(symbol),
        }
    
    def reset_symbol(self, symbol: str) -> None:
        """
        Сбросить статистику для символа
        """
        self.symbol_normal_spreads.pop(symbol, None)
        self.symbol_normal_volumes.pop(symbol, None)
        logger.info(f"Market conditions reset for {symbol}")
    
    def reset_all(self) -> None:
        """
        Сбросить всю статистику
        """
        self.symbol_normal_spreads.clear()
        self.symbol_normal_volumes.clear()
        self.btc_atr_pct = 0.0
        self.btc_last_updated = None
        logger.info("All market conditions reset")


# ═══════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════

_market_monitor: Optional[MarketConditionMonitor] = None


def get_market_monitor() -> MarketConditionMonitor:
    """Получить глобальный экземпляр монитора (singleton)"""
    global _market_monitor
    if _market_monitor is None:
        _market_monitor = MarketConditionMonitor()
    return _market_monitor