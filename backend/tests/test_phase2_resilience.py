"""
Phase 2 Unit Tests: Data Resilience & Storage Budget
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

# ── 1. Test Qualified Universe Filter ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_qualified_universe_filters_by_min_bars():
    from history_store import get_qualified_universe
    
    mock_coll = MagicMock()
    # Simulate aggregation returning only tickers with >= 500 bars
    mock_coll.aggregate.return_value.to_list = AsyncMock(return_value=[
        {"ticker": "RELIANCE.NS"},
        {"ticker": "TCS.NS"}
    ])
    
    with patch("history_store.get_ohlcv_history_collection", return_value=mock_coll):
        universe = await get_qualified_universe(min_bars=500)
        
    assert universe == ["RELIANCE.NS", "TCS.NS"]
    mock_coll.aggregate.assert_called_once()

# ── 2. Test Size Guard Pruning Logic ────────────────────────────────────────
@pytest.mark.asyncio
async def test_size_guard_prunes_when_over_threshold():
    from size_guard import check_and_enforce_storage_budget
    
    mock_db = MagicMock()
    # Simulate 450MB used (87% of 512MB)
    mock_db.command = AsyncMock(return_value={"dataSize": 400 * 1024 * 1024, "indexSize": 50 * 1024 * 1024})
    
    mock_coll = MagicMock()
    mock_coll.count_documents = AsyncMock(return_value=20000) # Over 15k limit
    
    # Simulate cutoff timestamp lookup
    mock_coll.find.return_value.sort.return_value.skip.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[{"timestamp": "2026-01-01T00:00:00Z"}]
    )
    mock_coll.delete_many = AsyncMock(return_value=MagicMock(deleted_count=5000))
    
    # Return the same mock_coll for all collections
    mock_db.__getitem__.return_value = mock_coll
    
    with patch("size_guard.get_db", return_value=mock_db):
        result = await check_and_enforce_storage_budget()
        
    assert result["status"] == "pruned"
    assert result["usage_pct"] > 0.85
    assert mock_coll.delete_many.await_count > 0

# ── 3. Test Honest Health Endpoint ───────────────────────────────────────────
def test_health_endpoint_returns_db_online_false_on_mongo_failure():
    from main import app
    
    with patch("main.ensure_connected", new_callable=AsyncMock, return_value=False), \
         patch("main.is_kill_switch_on", new_callable=AsyncMock, return_value=True):
        
        client = TestClient(app)
        resp = client.get("/api/health")
        
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_online"] is False
        assert data["kill_switch_active"] is True
        assert data["status"] == "degraded"

# ── 4. Test Global Exception Handler on DB Drops ─────────────────────────────
def test_mongo_exception_handler_returns_503():
    from fastapi import APIRouter
    from pymongo.errors import AutoReconnect
    from main import app

    router = APIRouter()

    @router.get("/api/test-db-fail")
    async def fail_route():
        raise AutoReconnect("connection lost")

    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/test-db-fail")
    assert resp.status_code == 503
    data = resp.json()
    assert data["db_online"] is False
    assert data["status"] == "degraded"
