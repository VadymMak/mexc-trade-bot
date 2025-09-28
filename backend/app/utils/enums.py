from enum import Enum

class ExchangeProvider(str, Enum):
    MEXC = "MEXC"
    BINANCE = "BINANCE"

class AccountMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"
    DEMO = "DEMO"
