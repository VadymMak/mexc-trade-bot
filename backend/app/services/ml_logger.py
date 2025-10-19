"""
ML Data Logger - собирает снапшоты для обучения модели.
"""

import asyncio
import time
import logging
from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import SessionLocal
from app.config.settings import settings
from app.market_data.book_tracker import book_tracker

logger = logging.getLogger("app.services.ml_logger")

class MLDataLogger:
    """
    Логирует рыночные снапшоты в ml_snapshots таблицу каждые N секунд.
    """
    
    def __init__(
        self,
        exchange: str = "mexc",
        symbols: List[str] = None,
        interval_sec: float = 2.0,
        enabled: bool = False,
    ):
        self.exchange = exchange.lower()
        self.symbols = symbols or []
        self.interval_sec = interval_sec
        self.enabled = enabled
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._snapshots_count = 0
        
        logger.info(
            f"MLDataLogger initialized: enabled={self.enabled}, "
            f"exchange={self.exchange}, symbols={len(self.symbols)}, "
            f"interval={self.interval_sec}s"
        )
    
    async def start(self):
        """Запустить фоновый сбор данных."""
        if not self.enabled:
            logger.warning(f"MLDataLogger is DISABLED (enabled={self.enabled})")
            return
        
        if not self.symbols:
            logger.warning(f"MLDataLogger has NO SYMBOLS (symbols={self.symbols})")
            return
        
        logger.info(
            f"🚀 MLDataLogger STARTING: {len(self.symbols)} symbols "
            f"({', '.join(self.symbols[:3])}{'...' if len(self.symbols) > 3 else ''}), "
            f"interval={self.interval_sec}s, exchange={self.exchange}"
        )
        
        self._running = True
        self._task = asyncio.create_task(self._collect_loop())
        
        logger.info("✅ MLDataLogger background task created")
    
    async def stop(self):
        """Остановить сбор."""
        logger.info(f"Stopping MLDataLogger (collected {self._snapshots_count} snapshots)")
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        logger.info("MLDataLogger stopped")
    
    async def _collect_loop(self):
        """Основной цикл сбора."""
        last_log_time = 0
        
        logger.info("MLDataLogger collect loop started")
        
        while self._running:
            try:
                collected = await self._collect_snapshot()
                
                if collected:
                    self._snapshots_count += collected
                
                # Логируем раз в минуту
                now = time.time()
                if now - last_log_time >= 60:
                    logger.info(
                        f"✓ MLDataLogger active: {self._snapshots_count} total snapshots, "
                        f"{len(self.symbols)} symbols"
                    )
                    last_log_time = now
                
                await asyncio.sleep(self.interval_sec)
                
            except asyncio.CancelledError:
                logger.info("MLDataLogger collect loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in MLDataLogger collect loop: {e}", exc_info=True)
                await asyncio.sleep(5)
        
        logger.info("MLDataLogger collect loop exited")
    
    async def _collect_snapshot(self) -> int:
        """
        Собрать один снапшот для всех символов.
        
        Returns:
            Количество успешно записанных снапшотов
        """
        ts_ms = int(time.time() * 1000)
        
        # Получить текущие quotes из book tracker
         
        quotes = {}
        for symbol in self.symbols:
            try:
                quote = await book_tracker.get_quote(symbol)
                if quote and quote.get("bid", 0) > 0 and quote.get("ask", 0) > 0:  # ✅ Validate data
                    quotes[symbol] = quote
            except Exception as e:
                logger.debug(f"Failed to get quote for {symbol}: {e}")
                continue  # ✅ Don't break the loop
        
        if not quotes:
            logger.debug(f"No quotes available yet for {self.exchange} (checked {len(self.symbols)} symbols)")
            return 0
        
        # Записать в БД
        db = SessionLocal()
        inserted = 0
        
        try:
            for symbol in self.symbols:
                quote = quotes.get(symbol)
                
                if not quote:
                    continue
                
                # Quote может быть dict или объект
                if isinstance(quote, dict):
                    bid = quote.get("bid")
                    ask = quote.get("ask")
                    last = quote.get("last")
                else:
                    # Если это объект, пробуем атрибуты
                    bid = getattr(quote, "bid", None)
                    ask = getattr(quote, "ask", None)
                    last = getattr(quote, "last", None)
                
                # Пропускаем если нет bid/ask
                if not bid or not ask or bid <= 0 or ask <= 0:
                    logger.debug(f"Skipping {symbol}: invalid bid/ask (bid={bid}, ask={ask})")
                    continue
                
                # Вычислить mid
                mid = (bid + ask) / 2
                
                # Вычислить offset (пример: -2 bps от ask, +2 bps от bid)
                your_offset_bps = 2.0
                
                snapshot = {
                    "ts": ts_ms,
                    "symbol": symbol,
                    "exchange": self.exchange,
                    
                    # Цены из SSE
                    "bid": float(bid),
                    "ask": float(ask),
                    "mid": float(mid),
                    "last": float(last) if last else None,
                    
                    # Метрики из scanner - пока NULL
                    "spread_bps": None,
                    "eff_spread_bps_maker": None,
                    "depth5_bid_usd": None,
                    "depth5_ask_usd": None,
                    "depth10_bid_usd": None,
                    "depth10_ask_usd": None,
                    "imbalance": None,
                    "trades_per_min": None,
                    "usd_per_min": None,
                    "median_trade_usd": None,
                    "atr1m_pct": None,
                    "grinder_ratio": None,
                    "pullback_median_retrace": None,
                    "spike_count_90m": None,
                    "imbalance_sigma_hits_60m": None,
                    "ws_lag_ms": None,
                    
                    # Maker-специфичные
                    "your_offset_bps": your_offset_bps,
                    "spread_volatility_5min": None,
                    
                    # Outcomes - NULL пока
                    "filled_20s": None,
                    "fill_time_s": None,
                    "mid_at_fill": None,
                    "mid_at_20s": None,
                    "profit_bps": None,
                    "exit_spread_bps": None,
                    
                    # Метаданные
                    "scanner_preset": settings.ML_LOGGING_PRESET,
                    "ml_version": "v0_collection",
                }
                
                # INSERT
                columns = ", ".join(snapshot.keys())
                placeholders = ", ".join([f":{k}" for k in snapshot.keys()])
                query = text(f"INSERT INTO ml_snapshots ({columns}) VALUES ({placeholders})")
                
                db.execute(query, snapshot)
                inserted += 1
            
            db.commit()
            
            if inserted > 0:
                logger.debug(f"Inserted {inserted} snapshots at {datetime.fromtimestamp(ts_ms/1000).strftime('%H:%M:%S')}")
        
        except Exception as e:
            logger.error(f"Error inserting snapshots: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()
        
        return inserted


# Singleton instance
_ml_logger: Optional[MLDataLogger] = None

def get_ml_logger() -> MLDataLogger:
    """Получить глобальный экземпляр ML logger."""
    global _ml_logger
    if _ml_logger is None:
        enabled = settings.ML_LOGGING_ENABLED
        symbols_str = settings.ML_LOGGING_SYMBOLS
        symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()] if symbols_str else []
        exchange = settings.active_provider.lower()
        interval = settings.ML_LOGGING_INTERVAL_SEC
        
        _ml_logger = MLDataLogger(
            exchange=exchange,
            symbols=symbols,
            interval_sec=interval,
            enabled=enabled,
        )
    return _ml_logger