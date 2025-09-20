# utils/symbols.py
QUOTE_SUFFIXES = ("USDT", "USDC", "BTC", "ETH")

def mexc_ws_symbol(sym: str) -> str:
    """
    MEXC WS uses BASE_QUOTE with an underscore, e.g. PLBUSDT -> PLB_USDT.
    """
    s = sym.upper().strip()
    for q in QUOTE_SUFFIXES:
        if s.endswith(q):
            base = s[:-len(q)]
            return f"{base}_{q}"
    return s
