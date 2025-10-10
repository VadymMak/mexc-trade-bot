# scripts/test_mexc_unlock.py
import pytest
from fastapi.testclient import TestClient

from app.main import app  # your FastAPI app

client = TestClient(app)

def test_mexc_scan_endpoint_smoke():
    # Align with your router paths printed in stdout:
    # /api/scanner/mexc/top exists; use that instead of /scanner/scan
    resp = client.get("/api/scanner/mexc/top", params={"limit": 5, "min_quote_vol_usd": 0})
    # Even if upstream exchanges are unreachable, API should return 200 with a JSON payload (possibly empty)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
