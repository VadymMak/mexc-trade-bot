from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    NEON_DATABASE_URL: str = ""
    # Internal Railway URL for service-to-service calls (no egress cost).
    # Set TRADING_BOT_URL_INTERNAL in Railway env to e.g.
    # http://backend.railway.internal:8000  — avoids $0.05/GB egress charges.
    # Falls back to TRADING_BOT_URL (public) if not set.
    TRADING_BOT_URL: str = "http://localhost:8000"
    TRADING_BOT_URL_INTERNAL: str = ""  # override in Railway → http://backend.railway.internal:8000
    SYMBOLS: str = "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT"

    @property
    def internal_url(self) -> str:
        """Return internal Railway URL if configured, else public URL."""
        return self.TRADING_BOT_URL_INTERNAL or self.TRADING_BOT_URL

    @property
    def symbols_list(self) -> list[str]:
        return [s.strip() for s in self.SYMBOLS.split(",") if s.strip()]
    MIN_SPREAD_PCT: float = 0.005          # 0.50% — raised from 0.30%: data analysis (2618 trades) shows <0.5% is net-negative (-$1.56, 53% WR), >=0.5% gives 93.8% WR same PnL (+$47.6)
    ZSCORE_THRESHOLD: float = 2.0         # lowered from 2.5: gate↔mexc futures pairs peak at z~2.44 and never cross 2.5; epoch-3 data shows 97.7% WR supports z>=2.0 entries
    MAX_SPREAD_LAG_MS: int = 500
    SPREAD_WINDOW_TICKS: int = 300
    # ── Position sizing (vel-based dynamic) ──────────────────────────────────
    PAPER_DEAL_SIZE_USDT: float = 10.0      # base size (vel < 10)
    DEAL_SIZE_MED_USDT: float = 20.0        # vel 10-50
    DEAL_SIZE_HIGH_USDT: float = 30.0       # vel > 50
    MAX_EXPOSURE_USDT: float = 150.0        # max total open exposure
    MAX_OPEN_POSITIONS: int = 8             # hard cap on simultaneous positions
    PROMOTE_THRESHOLD: float = 0.75
    MIN_TRADES_TO_PROMOTE: int = 50
    LOG_LEVEL: str = "INFO"
    SYMBOLS_FILE: str = "data/discovered_symbols.json"
    SCALP_ENABLED: bool = False        # set True to re-enable scalp trader

    # ── Exit strategy ────────────────────────────────────────────────────────
    # Take-profit: close when spread narrows by this fraction from entry
    # e.g. 0.50 → entered at 20%, exit at 10%
    TAKE_PROFIT_RATIO: float = 0.50

    # Stop-loss: close if spread grows by this multiplier from entry
    # e.g. 1.5 → entered at 0.50%, stop-loss at 0.75%
    # Reduced from 2.0: at 2.0× SL loss was 76× larger than TP win → terrible R/R

    # Max hold time in seconds — close regardless of spread/zscore
    # Default 4 hours = 14400s
    MAX_HOLD_SECONDS: int = 14_400

    # z-score exit threshold (spread reverted to mean)
    ZSCORE_EXIT: float = 0.5

    # Minimum hold time before ZSCORE_REVERT exit is allowed (seconds).
    # Z-score noise in the first 30-60s causes premature exits where spread
    # barely moved — paying double fees for near-zero PnL.
    # Data shows avg ZSCORE_REVERT hold = 30s, exit spread = 94% of entry.
    # Setting 120s forces the position to wait for genuine spread reversion.
    ZSCORE_REVERT_MIN_HOLD_SECONDS: int = 120

    # Max spread cap: ignore obviously bogus data (e.g. EDGE_USDT 759%)
    # Spreads above this value are price-scale mismatches, not real arb
    MAX_SPREAD_PCT: float = 50.0

    # Minimum spread_cv (coefficient of variation = std/mean) required to open.
    # Data analysis shows:
    #   cv < 0.5  → TP rate 11.6%, PnL -$112 on 6038 trades (structural noise)
    #   cv 1.0-1.5 → TP rate 73.4%, PnL +$7
    #   cv > 2.0  → TP rate 89%, PnL +$6-17
    # Low cv = spread is stable/structural (doesn't mean-revert) → don't trade
    # High cv = spread oscillates actively around mean → genuine arb opportunity
    MIN_SPREAD_CV: float = 1.0

    # Cooldown after STOP_LOSS (seconds): prevent immediate re-entry on volatile pairs
    STOP_LOSS_COOLDOWN_SECONDS: int = 300  # 5 minutes

    # Funding blackout: don't open positions within N seconds before funding time.
    # Perpetual futures funding is paid at 00:00, 08:00, 16:00 UTC (every 8h).
    # Opening just before funding = paying entry fees + funding before spread closes.
    FUNDING_BLACKOUT_SECONDS: int = 300  # 5 min before each funding window

    # Trading exchanges whitelist — ONLY open positions between these exchanges.
    # Binance and Bybit are mark-price references, NOT tradable venues.
    # Their prices structurally diverge from Gate/MEXC futures → phantom spreads.
    # KuCoin removed 2026-05-16: not actively trading, was wasting WS connections.
    TRADING_EXCHANGES: str = "gate,mexc"

    @property
    def trading_exchanges_set(self) -> set[str]:
        return {s.strip().lower() for s in self.TRADING_EXCHANGES.split(",") if s.strip()}

    # Collectors to enable. Comma-separated subset of:
    #   gate, mexc, binance, bybit, kucoin, mexc_spot
    # For gate↔mexc-only trading, binance/bybit/kucoin/mexc_spot are phantom
    # references — they consume WS connections and CPU with zero trading value.
    # Override via env: ENABLED_COLLECTORS=gate,mexc,binance
    ENABLED_COLLECTORS: str = "gate,mexc"

    @property
    def enabled_collectors_set(self) -> set[str]:
        return {s.strip().lower() for s in self.ENABLED_COLLECTORS.split(",") if s.strip()}

    # ── Dynamic MM sizing (tiered by entry spread) ───────────────────────────
    # Data analysis (2796 zscore trades) shows fee-to-gross ratio drops sharply
    # as spread grows: at >=2% spread, fees are only 12% of gross vs 104% at <0.5%.
    # Tiers multiply PAPER_DEAL_SIZE_USDT by the given factor.
    # Simulation result: flat $10 → net $53, dynamic tiers → net $143 (+169%).
    MM_TIER1_SPREAD_PCT: float = 1.0   # spread >= 1.0% → × MM_TIER1_MULT
    MM_TIER2_SPREAD_PCT: float = 1.5   # spread >= 1.5% → × MM_TIER2_MULT
    MM_TIER3_SPREAD_PCT: float = 2.0   # spread >= 2.0% → × MM_TIER3_MULT
    MM_TIER1_MULT: float = 2.0         # $10 × 2 = $20
    MM_TIER2_MULT: float = 3.0         # $10 × 3 = $30
    MM_TIER3_MULT: float = 5.0         # $10 × 5 = $50

    # ── Live / Testnet trading keys ──────────────────────────────────────────
    # Set LIVE_TRADING=true to activate real order execution.
    # Set GATE_TESTNET=true to test on Gate.io testnet (virtual funds).
    # When GATE_TESTNET=true, MEXC leg is replaced by a mock client (logs only).
    GATE_FUTURES_API_KEY: str = ""
    GATE_FUTURES_SECRET: str = ""
    GATE_FUTURES_TESTNET_API_KEY: str = ""
    GATE_FUTURES_TESTNET_SECRET: str = ""
    MEXC_FUTURES_API_KEY: str = ""
    MEXC_FUTURES_SECRET: str = ""

    # ── Dynamic symbol suspension ────────────────────────────────────────────
    # Tracks last N trades per symbol. If recent win-rate falls below threshold,
    # suspend the symbol temporarily (don't open new positions).
    # This catches coins that transition from profitable to structural/broken
    # faster than the static evaluator (which needs 50+ trades).
    #
    # Window: last 10 trades per symbol
    # Thresholds (checked after min_trades):
    #   WR < 30% in last 10  → suspend 4h  (struggling)
    #   WR =  0% in last 10  → suspend 8h  (clearly broken)
    # Counter resets after 1 profitable trade (so good coins aren't blocked long)
    DYNAMIC_SUSPEND_WINDOW:     int   = 10    # rolling window size
    DYNAMIC_SUSPEND_MIN_TRADES: int   = 5     # minimum trades before suspension kicks in
    DYNAMIC_SUSPEND_WR_LOW:     float = 0.30  # WR < 30% → 4h suspension
    DYNAMIC_SUSPEND_HOURS_LOW:  float = 4.0   # hours to suspend on WR < 30%
    DYNAMIC_SUSPEND_HOURS_ZERO: float = 8.0   # hours to suspend on WR = 0%

    # Blacklisted symbols — structurally wide spreads that never revert.
    # Identified from dataset: ~0-6% win rate across 80-170 trades each.
    # Stored as comma-separated string (pydantic-settings doesn't support list from env).
    # Updated 2026-05-12: added STO/NOM/CHILLGUY from 10h dataset analysis
    # STO: 4% WR (-$20/10h), NOM: 20% WR (-$18/10h), CHILLGUY: 7% WR (-$12/10h)
    BLACKLISTED_SYMBOLS: str = "ENJ_USDT,BLUR_USDT,ONT_USDT,DRIFT_USDT,ONG_USDT,SIREN_USDT,STO_USDT,NOM_USDT,CHILLGUY_USDT"

    @property
    def blacklisted_set(self) -> set[str]:
        return {s.strip().upper() for s in self.BLACKLISTED_SYMBOLS.split(",") if s.strip()}

    # Stop-loss ratio reduced: 2.0 meant loss = 2× potential win → terrible R/R.
    # At 1.5×: SL loss = entry×0.5×0.1+0.026 vs TP win = entry×0.5×0.1−0.026
    STOP_LOSS_RATIO: float = 1.5

    class Config:
        env_file = ".env"


settings = Settings()
