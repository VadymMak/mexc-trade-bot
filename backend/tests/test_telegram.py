import asyncio
import os
from pathlib import Path
import sys

# Добавить backend в путь
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# ═══ ЗАГРУЗКА .env ФАЙЛА ═══
from dotenv import load_dotenv

# .env находится в backend/ (на уровень выше tests/)
env_path = backend_dir / '.env'
load_dotenv(env_path)

print(f"🔍 Loading .env from: {env_path}")
print(f"📁 File exists: {env_path.exists()}")
print(f"TELEGRAM_ENABLED: {os.getenv('TELEGRAM_ENABLED')}")
print(f"TELEGRAM_BOT_TOKEN: {os.getenv('TELEGRAM_BOT_TOKEN')[:20]}..." if os.getenv('TELEGRAM_BOT_TOKEN') else "TELEGRAM_BOT_TOKEN: None")
print(f"TELEGRAM_CHAT_ID: {os.getenv('TELEGRAM_CHAT_ID')}")
print()

# ═══ ТЕСТ ПОДКЛЮЧЕНИЯ ═══
from app.services.telegram_bot import test_telegram_connection

async def main():
    result = await test_telegram_connection()
    if result:
        print("✅ Telegram bot works!")
    else:
        print("❌ Telegram bot failed")

if __name__ == "__main__":
    asyncio.run(main())