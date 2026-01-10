import sys
from pathlib import Path
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Add backend to path
backend_path = Path(__file__).parent
sys.path.insert(0, str(backend_path))

from app.db.session import SessionLocal
from app.models.ui_state import UIState
from app.models.strategy_state import StrategyState

def reset_persisted_symbols():
    """Clear all persisted state from database"""
    db = SessionLocal()
    try:
        # Clear UI state
        ui_count = db.query(UIState).delete()
        
        # Clear strategy state
        strategy_count = db.query(StrategyState).delete()
        
        db.commit()
        
        print(f"✅ Reset complete!")
        print(f"   - Deleted {ui_count} UI state records")
        print(f"   - Deleted {strategy_count} strategy state records")
        print(f"\n🔄 Now restart your backend to load symbols from .env")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    print("🧹 Clearing persisted symbols from database...")
    reset_persisted_symbols()