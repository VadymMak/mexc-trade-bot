"""
Exploration Mode - случайное изменение параметров для сбора разнообразных данных
ТОЛЬКО для Paper Trading!
"""
import random
import logging
from typing import Dict, Any, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ExplorationConfig:
    """Конфигурация exploration mode"""
    
    # Включен ли exploration mode (ТОЛЬКО для PAPER!)
    enabled: bool = True
    
    # Вероятность exploration vs exploitation
    exploration_rate: float = 0.3  # 30% случайные, 70% текущие лучшие
    
    # Диапазоны для случайных параметров
    tp_range: Tuple[float, float] = (1.0, 10.0)      # bps
    sl_range: Tuple[float, float] = (-10.0, -0.5)    # bps (отрицательные)
    trail_distance_range: Tuple[float, float] = (0.3, 2.0)  # bps
    trail_activation_range: Tuple[float, float] = (1.0, 5.0)  # bps
    timeout_range: Tuple[float, float] = (10.0, 60.0)  # seconds
    
    # Вероятность включения trailing stop
    trailing_probability: float = 0.5  # 50% шанс включить
    
    # Минимальное соотношение TP/SL (для безопасности)
    min_tp_sl_ratio: float = 0.8  # TP должен быть >= 80% от SL


class ExplorationManager:
    """
    Управляет exploration mode для сбора разнообразных данных
    """
    
    def __init__(self, config: ExplorationConfig = None):
        self.config = config or ExplorationConfig()
        self.exploration_count = 0
        self.exploitation_count = 0
    
    def should_explore(self) -> bool:
        """Решить: использовать случайные параметры или текущие лучшие?"""
        if not self.config.enabled:
            return False
        
        return random.random() < self.config.exploration_rate
    
    def get_random_params(
        self,
        default_params: Dict[str, Any] = None
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Получить параметры (случайные или дефолтные)
        
        Args:
            default_params: Текущие лучшие параметры (если есть)
        
        Returns:
            (params, is_exploration): параметры и флаг exploration
        """
        
        if not self.config.enabled or not self.should_explore():
            # Exploitation: используем текущие лучшие параметры
            self.exploitation_count += 1
            
            # ✅ ИСПРАВЛЕНО: Убедимся что все поля есть
            if default_params is None:
                return self._get_default_params(), False
            
            # Дополним недостающие поля из дефолтных
            full_params = self._get_default_params()
            full_params.update(default_params)
            
            return full_params, False
        
        # Exploration: генерируем случайные параметры
        self.exploration_count += 1
        
        params = self._generate_random_params()
        
        logger.info(
            f"[EXPLORATION] 🎲 Random params: TP={params['take_profit_bps']:.2f}, "
            f"SL={params['stop_loss_bps']:.2f}, "
            f"Trail={'ON' if params['trailing_stop_enabled'] else 'OFF'}"
        )
        
        return params, True
    
    def _generate_random_params(self) -> Dict[str, Any]:
        """Сгенерировать случайные параметры в заданных диапазонах"""
        
        # Stop Loss (всегда отрицательный)
        stop_loss_bps = random.uniform(*self.config.sl_range)
        
        # Take Profit (должен быть >= min_tp_sl_ratio * |SL|)
        min_tp = abs(stop_loss_bps) * self.config.min_tp_sl_ratio
        max_tp = self.config.tp_range[1]
        
        # Если min_tp больше max_tp, корректируем
        if min_tp > max_tp:
            take_profit_bps = min_tp
        else:
            take_profit_bps = random.uniform(min_tp, max_tp)
        
        # Trailing Stop
        trailing_enabled = random.random() < self.config.trailing_probability
        
        if trailing_enabled:
            trail_distance_bps = random.uniform(*self.config.trail_distance_range)
            trail_activation_bps = random.uniform(*self.config.trail_activation_range)
            
            # Trailing activation должен быть меньше TP
            trail_activation_bps = min(trail_activation_bps, take_profit_bps * 0.8)
        else:
            trail_distance_bps = 0.0
            trail_activation_bps = 0.0
        
        # Timeout
        timeout_seconds = random.uniform(*self.config.timeout_range)
        
        return {
            'take_profit_bps': round(take_profit_bps, 2),
            'stop_loss_bps': round(stop_loss_bps, 2),
            'trailing_stop_enabled': trailing_enabled,
            'trail_activation_bps': round(trail_activation_bps, 2),
            'trail_distance_bps': round(trail_distance_bps, 2),
            'timeout_seconds': round(timeout_seconds, 1)
        }
    
    def _get_default_params(self) -> Dict[str, Any]:
        """Дефолтные параметры (если не заданы)"""
        return {
            'take_profit_bps': 2.5,
            'stop_loss_bps': -3.0,
            'trailing_stop_enabled': True,
            'trail_activation_bps': 3.0,
            'trail_distance_bps': 0.5,
            'timeout_seconds': 30.0
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Статистика exploration/exploitation"""
        total = self.exploration_count + self.exploitation_count
        
        if total == 0:
            return {
                'total': 0,
                'exploration': 0,
                'exploitation': 0,
                'exploration_rate': 0.0
            }
        
        return {
            'total': total,
            'exploration': self.exploration_count,
            'exploitation': self.exploitation_count,
            'exploration_rate': self.exploration_count / total
        }
    
    def reset_stats(self):
        """Сбросить статистику"""
        self.exploration_count = 0
        self.exploitation_count = 0


# Global instance
exploration_manager = ExplorationManager()


def set_exploration_enabled(enabled: bool):
    """Включить/выключить exploration mode"""
    exploration_manager.config.enabled = enabled
    logger.info(f"[EXPLORATION] Mode {'ENABLED' if enabled else 'DISABLED'}")


def set_exploration_rate(rate: float):
    """Установить вероятность exploration (0.0 - 1.0)"""
    if not 0.0 <= rate <= 1.0:
        raise ValueError("Exploration rate must be between 0.0 and 1.0")
    
    exploration_manager.config.exploration_rate = rate
    logger.info(f"[EXPLORATION] Rate set to {rate:.1%}")


def get_params_for_trade(
    symbol: str,
    default_params: Dict[str, Any] = None
) -> Tuple[Dict[str, Any], bool]:
    """
    Получить параметры для новой сделки
    
    Args:
        symbol: Символ (для логирования)
        default_params: Текущие лучшие параметры
    
    Returns:
        (params, is_exploration): параметры и флаг exploration
    """
    params, is_exploration = exploration_manager.get_random_params(default_params)
    
    if is_exploration:
        logger.info(f"[EXPLORATION] {symbol}: Using random params")
    else:
        logger.debug(f"[EXPLOITATION] {symbol}: Using default params")
    
    return params, is_exploration


def get_exploration_stats() -> Dict[str, Any]:
    """Получить статистику exploration"""
    return exploration_manager.get_stats()


def print_exploration_summary():
    """Вывести summary exploration mode"""
    stats = get_exploration_stats()
    
    print("=" * 60)
    print("EXPLORATION MODE SUMMARY")
    print("=" * 60)
    print(f"Total trades:        {stats['total']}")
    print(f"Exploration trades:  {stats['exploration']} ({stats['exploration_rate']:.1%})")
    print(f"Exploitation trades: {stats['exploitation']} ({1-stats['exploration_rate']:.1%})")
    print("=" * 60)


# Example usage for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("TESTING EXPLORATION MODE")
    print("=" * 60)
    
    # Test 10 trades
    for i in range(10):
        params, is_exploration = get_params_for_trade(
            symbol="TESTUSDT",
            default_params={'take_profit_bps': 2.5, 'stop_loss_bps': -3.0}
        )
        
        mode = "🎲 EXPLORE" if is_exploration else "✅ EXPLOIT"
        print(f"\nTrade {i+1}: {mode}")
        print(f"  TP: {params['take_profit_bps']:.2f} bps")
        print(f"  SL: {params['stop_loss_bps']:.2f} bps")
        print(f"  Trailing: {params['trailing_stop_enabled']}")
    
    print("\n")
    print_exploration_summary()