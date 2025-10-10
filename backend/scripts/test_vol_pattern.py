# scripts/test_vol_pattern.py
from app.services.market_scanner import compute_vol_stability, compute_volatility_proxy

def test_vol_pattern():
    # Gate-like candle: [t, v_quote, o, h, l, c, v_base]
    # Keep base volumes fairly steady to get a higher stability score.
    candles = []
    t0 = 1_700_000_000_000
    for i in range(20):
        o = 100 + i * 0.01
        h = o + 0.05
        l = o - 0.05
        c = o + 0.02
        v_base = 100 + (i % 3)  # small variation
        candles.append([t0 + i * 60_000, 0.0, o, h, l, c, v_base])

    stability = compute_vol_stability(candles, is_candles=True, exchange="gate")
    vol_proxy = compute_volatility_proxy(candles, is_candles=True, exchange="gate")

    # Stability should be reasonable (>= 60) given mild variance
    assert isinstance(stability, int)
    assert 0 <= stability <= 100
    assert stability >= 60

    # Vol proxy should be small but positive given tight ranges
    assert isinstance(vol_proxy, float)
    assert vol_proxy >= 0.0
