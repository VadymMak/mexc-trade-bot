"""
Trade Tracker - отслеживает MFE/MAE и записывает в ml_trade_outcomes
"""
import asyncio
import time
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class TradeState:
    """Состояние активной сделки"""
    trade_id: str
    symbol: str
    exchange: str
    
    # Entry
    entry_time: datetime
    entry_price: float
    entry_qty: float
    entry_side: str  # 'BUY' or 'SELL'
    
    # Market conditions at entry
    market_snapshot: Dict[str, Any] = field(default_factory=dict)
    
    # Strategy params
    take_profit_bps: float = 0.0
    stop_loss_bps: float = 0.0
    trailing_stop_enabled: bool = False
    trail_activation_bps: float = 0.0
    trail_distance_bps: float = 0.0
    timeout_seconds: float = 30.0
    exploration_mode: int = 0
    
    # Tracking during trade
    peak_price: float = 0.0
    lowest_price: float = 0.0
    peak_time: Optional[datetime] = None
    lowest_time: Optional[datetime] = None
    
    # Current price samples
    price_samples: list = field(default_factory=list)
    
    def __post_init__(self):
        """Инициализация после создания"""
        if self.entry_side == 'BUY':
            self.peak_price = self.entry_price
            self.lowest_price = self.entry_price
        else:  # SELL
            self.peak_price = self.entry_price
            self.lowest_price = self.entry_price
        
        self.peak_time = self.entry_time
        self.lowest_time = self.entry_time
    
    def update_price(self, current_price: float, current_time: datetime):
        """Обновить цену и отследить MFE/MAE"""
        self.price_samples.append((current_time, current_price))
        
        if self.entry_side == 'BUY':
            # Для лонга: peak = максимум, lowest = минимум
            if current_price > self.peak_price:
                self.peak_price = current_price
                self.peak_time = current_time
            
            if current_price < self.lowest_price:
                self.lowest_price = current_price
                self.lowest_time = current_time
        
        else:  # SELL (short)
            # Для шорта: peak = минимум (лучшая цена для выкупа)
            if current_price < self.peak_price:
                self.peak_price = current_price
                self.peak_time = current_time
            
            if current_price > self.lowest_price:
                self.lowest_price = current_price
                self.lowest_time = current_time
    
    def get_mfe_bps(self) -> float:
        """Max Favorable Excursion (в bps)"""
        if self.entry_side == 'BUY':
            return ((self.peak_price - self.entry_price) / self.entry_price) * 10000
        else:  # SELL
            return ((self.entry_price - self.peak_price) / self.entry_price) * 10000
    
    def get_mae_bps(self) -> float:
        """Max Adverse Excursion (в bps, всегда отрицательный)"""
        if self.entry_side == 'BUY':
            return ((self.lowest_price - self.entry_price) / self.entry_price) * 10000
        else:  # SELL
            return ((self.entry_price - self.lowest_price) / self.entry_price) * 10000
    
    def get_optimal_tp_bps(self) -> float:
        """Оптимальный TP = где был пик"""
        return self.get_mfe_bps()
    
    def get_optimal_sl_bps(self) -> float:
        """Оптимальный SL = самый глубокий drawdown"""
        return self.get_mae_bps()


class TradeTracker:
    """
    Отслеживает активные сделки и собирает данные для ML
    """
    
    def __init__(self, db_path: str = "../backend/mexc.db"):
        self.db_path = db_path
        self.active_trades: Dict[str, TradeState] = {}
        self.running = False
        
    def start_tracking(
        self,
        trade_id: str,
        symbol: str,
        entry_price: float,
        entry_qty: float,
        entry_side: str,
        market_snapshot: Dict[str, Any],
        strategy_params: Dict[str, Any]
    ):
        """
        Начать отслеживание сделки
        
        Args:
            trade_id: Уникальный ID сделки
            symbol: Символ (например, LINKUSDT)
            entry_price: Цена входа
            entry_qty: Количество
            entry_side: 'BUY' или 'SELL'
            market_snapshot: Снапшот рынка при входе (из ml_snapshots)
            strategy_params: Параметры стратегии (TP, SL, trailing)
        """
        
        trade_state = TradeState(
            trade_id=trade_id,
            symbol=symbol,
            exchange='mexc',
            entry_time=datetime.now(timezone.utc),
            entry_price=entry_price,
            entry_qty=entry_qty,
            entry_side=entry_side,
            market_snapshot=market_snapshot,
            take_profit_bps=strategy_params.get('take_profit_bps', 0.0),
            stop_loss_bps=strategy_params.get('stop_loss_bps', 0.0),
            trailing_stop_enabled=strategy_params.get('trailing_stop_enabled', False),
            trail_activation_bps=strategy_params.get('trail_activation_bps', 0.0),
            trail_distance_bps=strategy_params.get('trail_distance_bps', 0.0),
            timeout_seconds=strategy_params.get('timeout_seconds', 30.0),
            exploration_mode=strategy_params.get('exploration_mode', 0)
        )
        
        self.active_trades[trade_id] = trade_state
        logger.info(f"[TRACKER] Started tracking {trade_id} ({symbol} {entry_side} @ {entry_price})")
    
    def update_trade_price(self, trade_id: str, current_price: float):
        """Обновить текущую цену для отслеживания MFE/MAE"""
        if trade_id not in self.active_trades:
            return
        
        trade = self.active_trades[trade_id]
        trade.update_price(current_price, datetime.now(timezone.utc))
    
    def stop_tracking(
        self,
        trade_id: str,
        exit_price: float,
        exit_qty: float,
        exit_reason: str,
        pnl_usd: float,
        pnl_bps: float
    ):
        """
        Остановить отслеживание и записать результат в БД
        
        Args:
            trade_id: ID сделки
            exit_price: Цена выхода
            exit_qty: Количество
            exit_reason: 'TP', 'SL', 'TRAIL', 'TIMEOUT', 'MANUAL'
            pnl_usd: P&L в USD
            pnl_bps: P&L в bps
        """
        
        if trade_id not in self.active_trades:
            logger.warning(f"[TRACKER] Trade {trade_id} not found in active trades")
            return
        
        trade = self.active_trades[trade_id]
        exit_time = datetime.now(timezone.utc)

        # Calculate metrics
        hold_duration_sec = (exit_time - trade.entry_time).total_seconds()
        mfe_bps = trade.get_mfe_bps()
        mae_bps = trade.get_mae_bps()
        optimal_tp_bps = trade.get_optimal_tp_bps()
        optimal_sl_bps = trade.get_optimal_sl_bps()
        
        # Determine if trailing was beneficial
        was_trailing_beneficial = 0
        if trade.trailing_stop_enabled and exit_reason == 'TRAIL':
            # Trailing помог если захватил больше чем фиксированный TP
            if pnl_bps > trade.take_profit_bps:
                was_trailing_beneficial = 1
        
        # Could have won?
        could_have_won = 0
        if pnl_bps < 0:  # Убыточная сделка
            # Могла бы быть прибыльной если бы достигла optimal TP
            if optimal_tp_bps > abs(trade.stop_loss_bps):
                could_have_won = 1
        
        # Write to database
        self._write_to_db(
            trade=trade,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_qty=exit_qty,
            exit_reason=exit_reason,
            pnl_usd=pnl_usd,
            pnl_bps=pnl_bps,
            hold_duration_sec=hold_duration_sec,
            mfe_bps=mfe_bps,
            mae_bps=mae_bps,
            optimal_tp_bps=optimal_tp_bps,
            optimal_sl_bps=optimal_sl_bps,
            was_trailing_beneficial=was_trailing_beneficial,
            could_have_won=could_have_won
        )
        
        # Remove from active trades
        del self.active_trades[trade_id]
        
        logger.info(
            f"[TRACKER] Stopped tracking {trade_id}: "
            f"PnL={pnl_bps:.2f}bps, MFE={mfe_bps:.2f}bps, MAE={mae_bps:.2f}bps, "
            f"Duration={hold_duration_sec:.1f}s, Reason={exit_reason}"
        )
    
    def _write_to_db(
        self,
        trade: TradeState,
        exit_time: datetime,
        exit_price: float,
        exit_qty: float,
        exit_reason: str,
        pnl_usd: float,
        pnl_bps: float,
        hold_duration_sec: float,
        mfe_bps: float,
        mae_bps: float,
        optimal_tp_bps: float,
        optimal_sl_bps: float,
        was_trailing_beneficial: int,
        could_have_won: int
    ):
        """Записать результат сделки в ml_trade_outcomes"""
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Extract market snapshot
            ms = trade.market_snapshot
            
            # Time context
            entry_dt = trade.entry_time
            hour_of_day = entry_dt.hour
            day_of_week = entry_dt.weekday()  # 0=Monday
            minute_of_hour = entry_dt.minute
            
            # Determine flags
            win = 1 if pnl_bps > 0 else 0
            hit_tp = 1 if exit_reason == 'TP' else 0
            hit_sl = 1 if exit_reason == 'SL' else 0
            hit_trailing = 1 if exit_reason == 'TRAIL' else 0
            timed_out = 1 if exit_reason == 'TIMEOUT' else 0
            
            cursor.execute("""
                INSERT INTO ml_trade_outcomes (
                    trade_id, symbol, exchange, workspace_id,
                    entry_time, entry_price, entry_qty, entry_side,
                    spread_bps_entry, eff_spread_bps_entry,
                    depth5_bid_usd_entry, depth5_ask_usd_entry,
                    depth10_bid_usd_entry, depth10_ask_usd_entry,
                    imbalance_entry, atr1m_pct_entry, grinder_ratio_entry,
                    pullback_median_retrace_entry,
                    trades_per_min_entry, usd_per_min_entry, median_trade_usd_entry,
                    hour_of_day, day_of_week, minute_of_hour,
                    take_profit_bps, stop_loss_bps,
                    trailing_stop_enabled, trail_activation_bps, trail_distance_bps,
                    timeout_seconds,
                    exit_time, exit_price, exit_qty, exit_reason,
                    pnl_usd, pnl_bps, pnl_percent, hold_duration_sec,
                    max_favorable_excursion_bps, max_adverse_excursion_bps,
                    peak_price, lowest_price, peak_time, lowest_time,
                    optimal_tp_bps, optimal_sl_bps,
                    was_trailing_beneficial, could_have_won,
                    win, hit_tp, hit_sl, hit_trailing, timed_out,
                    strategy_tag, exploration_mode, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id, trade.symbol, trade.exchange, 1,
                trade.entry_time.isoformat(), trade.entry_price, trade.entry_qty, trade.entry_side,
                ms.get('spread_bps', 0), ms.get('eff_spread_bps_maker', 0),
                ms.get('depth5_bid_usd', 0), ms.get('depth5_ask_usd', 0),
                ms.get('depth10_bid_usd', 0), ms.get('depth10_ask_usd', 0),
                ms.get('imbalance', 0.5), ms.get('atr1m_pct', 0), ms.get('grinder_ratio', 0),
                ms.get('pullback_median_retrace', 0),
                ms.get('trades_per_min', 0), ms.get('usd_per_min', 0), ms.get('median_trade_usd', 0),
                hour_of_day, day_of_week, minute_of_hour,
                trade.take_profit_bps, trade.stop_loss_bps,
                1 if trade.trailing_stop_enabled else 0,
                trade.trail_activation_bps, trade.trail_distance_bps,
                trade.timeout_seconds,
                exit_time.isoformat(), exit_price, exit_qty, exit_reason,
                pnl_usd, pnl_bps, (pnl_bps / 100.0), hold_duration_sec,
                mfe_bps, mae_bps,
                trade.peak_price, trade.lowest_price,
                trade.peak_time.isoformat() if trade.peak_time else None,
                trade.lowest_time.isoformat() if trade.lowest_time else None,
                optimal_tp_bps, optimal_sl_bps,
                was_trailing_beneficial, could_have_won,
                win, hit_tp, hit_sl, hit_trailing, timed_out,
                'paper_hedgehog', trade.exploration_mode, None
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"[TRACKER] ✅ Записано в ml_trade_outcomes: {trade.trade_id}")
            
        except Exception as e:
            logger.error(f"[TRACKER] ❌ Ошибка записи в БД: {e}", exc_info=True)
    
    def get_active_count(self) -> int:
        """Количество активных отслеживаемых сделок"""
        return len(self.active_trades)


# Global instance
tracker = TradeTracker()