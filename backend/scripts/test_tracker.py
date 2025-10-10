import sys
from pathlib import Path

# Fix: Add the project root (backend/) to Python path if needed
project_root = Path(__file__).parent.parent  # scripts/ -> backend/
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

print(f"Script path: {Path(__file__).resolve()}")
print(f"Project root added to sys.path: {project_root}")
print("sys.path includes:", [p for p in sys.path if 'backend' in str(p).lower() or 'projects' in str(p).lower()])

from app.services.book_tracker import book_tracker
print('Import OK - book_tracker loaded!')

import asyncio

async def t():
    print('Inside async func - calling update...')
    await book_tracker.update_book_ticker('PLBUSDT', 100, 2, 101, 3)
    print('Update OK - ticker updated!')

print('About to run asyncio...')
asyncio.run(t())
print('Script finished - all good!')