# tests/test_market_scanner_unit.py
import math
import pytest

from app.services.market_scanner import (
    ScanRow,
    _to_pair, _from_pair, _split_pair,
    _calc_effective_spreads, _absorption_usd_in_band,
    _compute_liquidity_grade, _classify_reason
)

def test_symbol_helpers():
    assert _to_pair("ethusdt") == "ETH_USDT"
    assert _to_pair("ETH/USDT") == "ETH_USDT"
    assert _to_pair("ETH-USDT") == "ETH_USDT"
    assert _from_pair("ETH_USDT") == "ETHUSDT"
    assert _split_pair("ETH_USDT") == ("ETH", "USDT")
    assert _split_pair("BTCUSDT") == ("BTC", "USDT")

def test_effective_spreads_math():
    # spread 0.5 bps, taker fee 5 bps → taker = 0.5 + 10 = 10.5 bps, maker = max(0.5 - 0, 0) = 0.5
    eff_m, eff_t = _calc_effective_spreads(0.5, maker_fee=0.0, taker_fee=0.0005)
    assert math.isclose(eff_m, 0.5, rel_tol=1e-6)
    assert math.isclose(eff_t, 10.5, rel_tol=1e-6)

def test_absorption_band():
    bids = [(100.0, 2.0), (99.5, 1.0), (98.0, 5.0)]   # price, qty
    asks = [(100.5, 1.5), (101.0, 2.0), (102.0, 3.0)]
    mid = 100.25
    b_usd, a_usd = _absorption_usd_in_band(bids, asks, mid, 50)  # 50 bps band ~ +/-0.5%
    assert b_usd > 0.0 and a_usd > 0.0
    # Within ±50 bps of 100.25 (floor≈99.7475, cap≈100.75125) → include 100.0*2.0 only; 99.5 is out of band.
    # Ask side includes 100.5*1.5 = 150.75.
    assert abs(b_usd - 200.0) < 1e-9
    assert a_usd >= 150.75 - 1e-9

def test_liquidity_grade():
    assert _compute_liquidity_grade(6000) == "A"
    assert _compute_liquidity_grade(3000) == "B"
    assert _compute_liquidity_grade(1500) == "C"

def test_reason_classifier_spread_wide():
    row = ScanRow(symbol="ETHUSDT", bid=100.0, ask=101.0)
    # minimal stage fields for spread calc
    from app.services.market_scanner import _apply_stage1_fields_and_effective
    _apply_stage1_fields_and_effective(row)
    _classify_reason(
        row,
        min_depth5_usd=0.0,
        min_trades_per_min=0.0,
        min_usd_per_min=0.0,
        spread_cap_bps=5.0,  # 100 bps spread here
        explain=True
    )
    assert row.reason == "spread too wide"
    assert any("spread_bps" in r for r in row.reasons_all)

def test_scanrow_to_from_dict_aliases():
    row = ScanRow(symbol="BTCUSDT", eff_spread_taker_bps=10.1, eff_spread_maker_bps=0.5)
    d = row.to_dict()
    assert "eff_taker_bps" in d and "eff_maker_bps" in d
    row2 = ScanRow.from_dict({"symbol": "BTCUSDT", "eff_taker_bps": 9.9, "eff_maker_bps": 0.4})
    assert row2.eff_spread_taker_bps == 9.9
    assert row2.eff_spread_maker_bps == 0.4

def test_score_prefers_tighter_spread(monkeypatch):
    from app.services.market_scanner import _score_row
    a = ScanRow(symbol="X", usd_per_min=5000.0, depth_at_bps={5: {"bid_usd": 5000, "ask_usd": 5000}}, spread_bps=0.2)
    b = ScanRow(symbol="Y", usd_per_min=5000.0, depth_at_bps={5: {"bid_usd": 5000, "ask_usd": 5000}}, spread_bps=5.0)
    # same other inputs → lower spread should score higher
    a.score = _score_row(a)
    b.score = _score_row(b)
    assert a.score > b.score
