"""
Risk Management API Router
API для управления рисками
"""

from fastapi import APIRouter, HTTPException, Header
from typing import Optional
import logging

from app.strategy.risk import get_risk_manager
from app.services.alerts import send_test_alert

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk", tags=["risk"])


# ═══════════════════════════════════════════════════════════
# STATUS & DIAGNOSTICS
# ═══════════════════════════════════════════════════════════

@router.get("/status")
async def get_risk_status():
    """
    Получить текущий статус рисков
    
    Returns:
        - trading_allowed: разрешена ли торговля
        - trading_halted: остановлена ли торговля
        - halt_reason: причина остановки
        - daily_pnl_usd: дневной P&L
        - daily_loss_limit_usd: лимит убытков
        - active_cooldowns: список символов на cooldown
        - positions/exposure: текущие позиции
        - velocity: трейды за час/минуту
    """
    try:
        risk_manager = get_risk_manager()
        status = risk_manager.get_status()
        return status
    except Exception as e:
        logger.error(f"Error getting risk status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/limits")
async def get_risk_limits():
    """
    Получить все лимиты рисков
    
    Returns:
        Все настроенные лимиты (daily loss, position limits, velocity, etc)
    """
    try:
        risk_manager = get_risk_manager()
        limits = risk_manager.get_limits()
        return limits
    except Exception as e:
        logger.error(f"Error getting risk limits: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# EMERGENCY CONTROLS
# ═══════════════════════════════════════════════════════════

@router.post("/panic")
async def panic_button(
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key")
):
    """
    🚨 ПАНИЧЕСКАЯ КНОПКА
    
    Действия:
    1. Halt всей торговли
    2. Закрыть все открытые позиции (если executor доступен)
    3. Отправить критичный алерт
    
    Returns:
        - status: "halted"
        - positions_closed: количество закрытых позиций
        - timestamp: время остановки
    """
    try:
        risk_manager = get_risk_manager()
        
        # Emergency stop (пока без executor - просто halt)
        # TODO: передать executor когда будет интеграция
        closed_count = await risk_manager.emergency_stop(executor=None)
        
        logger.critical(f"🚨 PANIC BUTTON ACTIVATED (positions_closed={closed_count})")
        
        return {
            "status": "halted",
            "positions_closed": closed_count,
            "halt_reason": "emergency_stop",
            "timestamp": risk_manager.state.halted_at.isoformat() if risk_manager.state.halted_at else None
        }
    
    except Exception as e:
        logger.error(f"Error in panic button: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resume")
async def resume_trading(
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key")
):
    """
    ▶️ Возобновить торговлю
    
    Снимает halt флаг и разрешает торговлю снова.
    
    Returns:
        - status: "resumed"
        - trading_allowed: True
    """
    try:
        risk_manager = get_risk_manager()
        
        if not risk_manager.state.trading_halted:
            return {
                "status": "already_active",
                "trading_allowed": True,
                "message": "Trading is not halted"
            }
        
        await risk_manager.resume_trading()
        
        logger.info("✅ Trading resumed via API")
        
        return {
            "status": "resumed",
            "trading_allowed": True
        }
    
    except Exception as e:
        logger.error(f"Error resuming trading: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# COOLDOWN MANAGEMENT
# ═══════════════════════════════════════════════════════════

@router.get("/cooldowns")
async def get_active_cooldowns():
    """
    Получить список активных cooldown'ов
    
    Returns:
        List of {symbol, until, remaining_sec}
    """
    try:
        risk_manager = get_risk_manager()
        cooldowns = risk_manager.get_active_cooldowns()
        
        return {
            "cooldowns": [
                {
                    "symbol": sym,
                    "until": until.isoformat(),
                    "remaining_sec": risk_manager.state.get_cooldown_remaining_seconds(sym)
                }
                for sym, until in cooldowns
            ]
        }
    
    except Exception as e:
        logger.error(f"Error getting cooldowns: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cooldowns/{symbol}")
async def clear_cooldown(symbol: str):
    """
    Очистить cooldown для символа
    
    Args:
        symbol: Символ (BTCUSDT)
    """
    try:
        risk_manager = get_risk_manager()
        
        symbol_upper = symbol.upper()
        
        if not risk_manager.state.is_symbol_on_cooldown(symbol_upper):
            return {
                "status": "not_on_cooldown",
                "symbol": symbol_upper,
                "message": "Symbol is not on cooldown"
            }
        
        await risk_manager.clear_cooldown(symbol_upper)
        
        logger.info(f"✅ Cooldown cleared for {symbol_upper} via API")
        
        return {
            "status": "cleared",
            "symbol": symbol_upper
        }
    
    except Exception as e:
        logger.error(f"Error clearing cooldown: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# SETTINGS UPDATE
# ═══════════════════════════════════════════════════════════

@router.put("/limits")
async def update_risk_limits(payload: dict):
    """
    Обновить лимиты рисков
    
    Body: {
        "daily_loss_limit_pct": 2.0,
        "symbol_max_losses": 3,
        "account_balance_usd": 1000.0,
        ...
    }
    
    Returns:
        Обновлённые лимиты
    """
    try:
        risk_manager = get_risk_manager()
        
        # Обновление баланса (если указан)
        if "account_balance_usd" in payload:
            new_balance = float(payload["account_balance_usd"])
            if new_balance > 0:
                risk_manager.update_balance(new_balance)
                logger.info(f"✅ Balance updated to ${new_balance:.2f}")
        
        # Обновление других настроек
        settings = risk_manager.settings
        
        updatable_fields = [
            "daily_loss_limit_pct",
            "symbol_max_losses",
            "symbol_cooldown_minutes",
            "max_exposure_per_position_pct",
            "max_trades_per_hour",
            "max_trades_per_minute",
            "trading_hours_enabled",
            "btc_atr_threshold_pct",
            "spread_widening_multiplier",
            "volume_drop_threshold_pct"
        ]
        
        updated = []
        for field in updatable_fields:
            if field in payload:
                try:
                    setattr(settings, field, payload[field])
                    updated.append(field)
                except Exception as e:
                    logger.warning(f"Failed to update {field}: {e}")
        
        logger.info(f"✅ Risk limits updated: {updated}")
        
        return {
            "status": "updated",
            "updated_fields": updated,
            "limits": risk_manager.get_limits()
        }
    
    except Exception as e:
        logger.error(f"Error updating risk limits: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# ALERTS TEST
# ═══════════════════════════════════════════════════════════

@router.post("/alerts/test")
async def test_alert():
    """
    Отправить тестовый алерт в Telegram
    
    Returns:
        - success: True/False
        - message: результат отправки
    """
    try:
        success = await send_test_alert()
        
        if success:
            return {
                "success": True,
                "message": "Test alert sent to Telegram"
            }
        else:
            return {
                "success": False,
                "message": "Failed to send alert (check Telegram settings)"
            }
    
    except Exception as e:
        logger.error(f"Error sending test alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))