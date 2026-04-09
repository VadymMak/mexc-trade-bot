from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    NEON_DATABASE_URL: str = ""
    TRADING_BOT_URL: str = "http://localhost:8000"
    SYMBOLS: str = "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT"

    @property
    def symbols_list(self) -> list[str]:
        return [s.strip() for s in self.SYMBOLS.split(",") if s.strip()]
    MIN_SPREAD_PCT: float = 0.0012        # 0.12% — minimum spread to consider entry
    ZSCORE_THRESHOLD: float = 2.5         # z-score entry threshold (mean-reversion)
    MAX_SPREAD_LAG_MS: int = 500
    SPREAD_WINDOW_TICKS: int = 300
    PAPER_DEAL_SIZE_USDT: float = 10.0
    PROMOTE_THRESHOLD: float = 0.75
    MIN_TRADES_TO_PROMOTE: int = 50
    LOG_LEVEL: str = "INFO"
    SYMBOLS_FILE: str = "data/discovered_symbols.json"

    # ── Exit strategy ────────────────────────────────────────────────────────
    # Take-profit: close when spread narrows by this fraction from entry
    # e.g. 0.50 → entered at 20%, exit at 10%
    TAKE_PROFIT_RATIO: float = 0.50

    # Stop-loss: close if spread grows by this multiplier from entry
    # e.g. 2.0 → entered at 20%, stop-loss at 40%
    STOP_LOSS_RATIO: float = 2.0

    # Max hold time in seconds — close regardless of spread/zscore
    # Default 4 hours = 14400s
    MAX_HOLD_SECONDS: int = 14_400

    # z-score exit threshold (spread reverted to mean)
    ZSCORE_EXIT: float = 0.5

    class Config:
        env_file = ".env"


settings = Settings()
