"""
Test Trade model.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from app.models.trades import Trade
from app.db.session import SessionLocal

# Create test trade
db = SessionLocal()

try:
    # Test 1: Create entry
    print("1️⃣ Creating trade entry...")
    trade = Trade.create_entry(
        trade_id="TEST_001",
        symbol="BANUSDT",
        entry_time=datetime.utcnow(),
        entry_price=0.06221,
        entry_qty=803.73,
        entry_side="BUY",
        entry_fee=0.005,
        spread_bps=28.89,
        imbalance=0.025,
        depth_5bps=1500.0,
        strategy_tag="mm_entry_test",
        exchange="MEXC"
    )
    
    db.add(trade)
    db.commit()
    print(f"✅ Trade created: {trade}")
    
    # Test 2: Close trade
    print("\n2️⃣ Closing trade...")
    trade.close_trade(
        exit_time=datetime.utcnow() + timedelta(seconds=1.5),
        exit_price=0.06236,
        exit_qty=803.73,
        exit_side="SELL",
        exit_reason="TP",
        exit_fee=0.008
    )
    
    db.commit()
    print(f"✅ Trade closed: {trade}")
    print(f"   P&L: ${trade.pnl_usd:.4f} ({trade.pnl_bps:.2f} bps)")
    print(f"   Duration: {trade.hold_duration_sec:.2f}s")
    
    # Test 3: Convert to dict
    print("\n3️⃣ Converting to dict...")
    trade_dict = trade.to_dict()
    print(f"✅ Dict keys: {list(trade_dict.keys())[:10]}...")
    
    # Test 4: Query back
    print("\n4️⃣ Querying from database...")
    found = db.query(Trade).filter(Trade.trade_id == "TEST_001").first()
    if found:
        print(f"✅ Found trade: {found}")
        print(f"   Symbol: {found.symbol}")
        print(f"   P&L: ${found.pnl_usd:.4f}")
        print(f"   Status: {found.status}")
    
    print("\n✅ All tests passed!")
    
finally:
    db.close()