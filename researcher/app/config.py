from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    NEON_DATABASE_URL: str = ""
    TRADING_BOT_URL: str = "http://localhost:8000"
    SYMBOLS: str = "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT"

    @property
    def symbols_list(self) -> list[str]:
        return [s.strip() for s in self.SYMBOLS.split(",") if s.strip()]
    MIN_SPREAD_PCT: float = 0.0012        # 0.12%
    ZSCORE_THRESHOLD: float = 2.5
    MAX_SPREAD_LAG_MS: int = 30
    SPREAD_WINDOW_TICKS: int = 300
    PAPER_DEAL_SIZE_USDT: float = 10.0
    PROMOTE_THRESHOLD: float = 0.75
    MIN_TRADES_TO_PROMOTE: int = 50
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
