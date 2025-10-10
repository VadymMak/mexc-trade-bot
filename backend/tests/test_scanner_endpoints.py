# tests/test_scanner_endpoints.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_scanner_routes_are_mounted():
    # sanity: docs loads
    r = client.get("/docs")
    assert r.status_code == 200

def test_mexc_top_endpoint_smoke():
    r = client.get("/api/scanner/mexc/top", params={"limit": 3})
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_gate_top_endpoint_smoke():
    r = client.get("/api/scanner/gate/top", params={"limit": 3})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
