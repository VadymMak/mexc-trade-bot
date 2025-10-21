import asyncio
import os
from pathlib import Path
import sys
from dotenv import load_dotenv

# Добавить backend в путь (ДО импорта app!)
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Загрузка .env
env_path = backend_dir / '.env'
load_dotenv(env_path)

from app.services.alerts import (
    alert_daily_loss_limit,
    alert_symbol_cooldown,
    alert_trading_resumed,
    alert_ws_disconnect,
    alert_system_error,
    alert_emergency_stop,
    alert_profit_target,
    send_test_alert
)

async def test_all_alerts():
    print("🧪 Testing Telegram Alerts...\n")
    
    # 1. Test alert
    print("1️⃣ Sending test alert...")
    await send_test_alert()
    await asyncio.sleep(2)
    
    # 2. Daily loss limit
    print("2️⃣ Sending daily loss limit alert...")
    await alert_daily_loss_limit(pnl_usd=-25.50, limit_usd=20.00)
    await asyncio.sleep(2)
    
    # 3. Symbol cooldown
    print("3️⃣ Sending symbol cooldown alert...")
    await alert_symbol_cooldown(symbol="BTCUSDT", minutes=60)
    await asyncio.sleep(2)
    
    # 4. Trading resumed
    print("4️⃣ Sending trading resumed alert...")
    await alert_trading_resumed()
    await asyncio.sleep(2)
    
    # 5. WS disconnect
    print("5️⃣ Sending WS disconnect alert...")
    await alert_ws_disconnect(provider="MEXC", duration_sec=45)
    await asyncio.sleep(2)
    
    # 6. System error
    print("6️⃣ Sending system error alert...")
    await alert_system_error(
        module="strategy.engine",
        error="Division by zero",
        traceback="File risk.py, line 123\n  result = x / 0"
    )
    await asyncio.sleep(2)
    
    # 7. Emergency stop
    print("7️⃣ Sending emergency stop alert...")
    await alert_emergency_stop(positions_closed=3)
    await asyncio.sleep(2)
    
    # 8. Profit target (опционально)
    print("8️⃣ Sending profit target alert...")
    await alert_profit_target(pnl_usd=35.50, target_usd=30.00)
    
    print("\n✅ All alerts sent! Check your Telegram!")

if __name__ == "__main__":
    asyncio.run(test_all_alerts())