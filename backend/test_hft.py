# test_hft.py
"""
Test HFT Strategy Engine with Position Slots
Run: python test_hft.py
"""

import asyncio
import time
from app.strategy.engine_hft import HFTStrategyEngine
from app.execution.paper_executor import PaperExecutor
from app.db.session import SessionLocal

async def test_hft():
    print("🚀 Starting HFT Engine Test...")
    
    # Create paper executor
    executor = PaperExecutor(
        session_factory=SessionLocal,
        workspace_id=1
    )
    
    # Create HFT engine
    hft_engine = HFTStrategyEngine(
    executor=executor,
    symbols=["LINKUSDT", "VETUSDT", "ALGOUSDT", "NEARUSDT", "AVAXUSDT"],
    max_slots_per_symbol=8,
    target_size_usd=10.0,
    tp_bps=2.0,
    sl_bps=3.0,
    timeout_sec=15,
    cycle_ms=100,
    min_spread_bps=2.0,          
    edge_floor_bps=1.5,          
    entry_score_threshold=0.5    
)
    
    # Start
    await hft_engine.start_all()
    
    print("⏳ Running for 5 minutes (press Ctrl+C to stop early)...")
    
    start_time = time.time()
    target_duration = 300  # 5 минут
    
    try:
        while (time.time() - start_time) < target_duration:
            # Sleep in small chunks
            await asyncio.sleep(1)
            
            # Check if main task still running
            if hft_engine._main_task.done():
                print("[TEST] ⚠️ Main task stopped unexpectedly!")
                exc = hft_engine._main_task.exception()
                if exc:
                    print(f"[TEST] Exception: {exc}")
                break
                
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
    
    # Stop
    print("⏹️ Stopping HFT engine...")
    await hft_engine.stop_all()
    
    print("✅ Test complete!")

if __name__ == "__main__":
    asyncio.run(test_hft())