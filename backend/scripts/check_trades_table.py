"""
Check if trades table exists and show structure.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.engine import engine
from sqlalchemy import inspect

inspector = inspect(engine)
tables = inspector.get_table_names()

print("📊 All tables:", tables)

if 'trades' in tables:
    print("\n✅ 'trades' table exists!")
    
    columns = inspector.get_columns('trades')
    print(f"\n📋 Columns ({len(columns)}):")
    for col in columns:
        nullable = "NULL" if col.get('nullable', True) else "NOT NULL"
        default = f" DEFAULT {col.get('default')}" if col.get('default') else ""
        print(f"  {col['name']:<20} {str(col['type']):<15} {nullable}{default}")
else:
    print("\n❌ 'trades' table NOT found!")