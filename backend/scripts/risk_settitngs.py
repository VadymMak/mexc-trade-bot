import os

print("=" * 60)
print("ТЕКУЩИЕ НАСТРОЙКИ РИСКА")
print("=" * 60)

# Проверка из переменных окружения
max_exp = os.getenv("MAX_EXPOSURE_USD", "НЕ УСТАНОВЛЕНО")
max_sym = os.getenv("MAX_PER_SYMBOL_USD", "НЕ УСТАНОВЛЕНО")

print(f"\nMAX_EXPOSURE_USD:     {max_exp}")
print(f"MAX_PER_SYMBOL_USD:   {max_sym}")

# Проверка из settings
try:
    from app.config.settings import settings
    print(f"\nИз settings.py:")
    print(f"  max_exposure_usd:   {getattr(settings, 'max_exposure_usd', 'НЕ УСТАНОВЛЕНО')}")
except Exception as e:
    print(f"\nОшибка чтения settings: {e}")

print("\n" + "=" * 60)
print("РЕКОМЕНДАЦИИ ДЛЯ $1000 ДЕПОЗИТА:")
print("=" * 60)
print("\nКонсервативно (рекомендую):")
print("  MAX_EXPOSURE_USD=250")
print("  MAX_PER_SYMBOL_USD=50")
print("\nУмеренно:")
print("  MAX_EXPOSURE_USD=500")
print("  MAX_PER_SYMBOL_USD=100")
print("=" * 60)
