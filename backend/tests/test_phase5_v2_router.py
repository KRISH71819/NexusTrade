"""
Phase 5 Unit Tests: V2 API Router Global Contract
Verifies that all 9 V2 core endpoints inject `db_online` and `kill_switch_active`
telemetry, and never throw 500 errors on database failure.
"""

from unittest.mock import patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ── Test All 9 Endpoints Inject Telemetry ────────────────────────────────────

def test_v2_health_telemetry(client):
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False):
        response = client.get("/api/v2/health")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        assert data["kill_switch_active"] is False
        assert data["status"] == "healthy"
        assert data["service"] == "NexusTrade AI Agent V2"


def test_v2_portfolio_telemetry(client):
    dummy_portfolio = {
        "cash": 100000.0,
        "holdings": [],
        "total_value": 100000.0,
        "unrealized_pnl": 0.0,
        "mode": "paper",
    }
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False), \
         patch("routers.portfolio.get_portfolio_state", new_callable=AsyncMock, return_value=dummy_portfolio):
        response = client.get("/api/v2/portfolio")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        assert data["kill_switch_active"] is False
        assert data["cash"] == 100000.0


def test_v2_portfolio_history_telemetry(client):
    dummy_history = {"history": [], "count": 0, "mode": "paper"}
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False), \
         patch("routers.portfolio.get_portfolio_history", new_callable=AsyncMock, return_value=dummy_history):
        response = client.get("/api/v2/portfolio/history?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        assert data["kill_switch_active"] is False
        assert "history" in data


def test_v2_analysis_latest_telemetry(client):
    dummy_analysis = {"analyses": [], "count": 0}
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False), \
         patch("routers.analysis.get_latest_analyses", new_callable=AsyncMock, return_value=dummy_analysis):
        response = client.get("/api/v2/analysis/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        assert data["kill_switch_active"] is False
        assert "analyses" in data


def test_v2_trades_telemetry(client):
    dummy_trades = {"trades": [], "total_count": 0, "limit": 50, "offset": 0, "mode": "paper"}
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False), \
         patch("routers.trades.get_all_trades", new_callable=AsyncMock, return_value=dummy_trades):
        response = client.get("/api/v2/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        assert data["kill_switch_active"] is False
        assert "trades" in data


def test_v2_news_telemetry(client):
    dummy_news = {"macro_news": [], "crisis_alerts": [], "items": []}
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False), \
         patch("routers.news.get_latest_news", new_callable=AsyncMock, return_value=dummy_news):
        response = client.get("/api/v2/news")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        assert data["kill_switch_active"] is False
        assert "macro_news" in data


def test_v2_analytics_pnl_telemetry(client):
    dummy_pnl = {"daily_pnl": 0.0, "weekly_pnl": 0.0, "yearly_pnl": 0.0}
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False), \
         patch("routers.analytics.get_pnl_analytics", new_callable=AsyncMock, return_value=dummy_pnl):
        response = client.get("/api/v2/analytics/pnl")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        assert data["kill_switch_active"] is False
        assert "daily_pnl" in data


def test_v2_meta_summary_telemetry_overwrites_legacy(client):
    # If legacy meta_summary returns stale or conflicting kill_switch_active, V2 strictly overwrites it
    dummy_meta = {
        "status": "ready",
        "equity": 100000.0,
        "kill_switch_active": True,  # Legacy might say True
    }
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False), \
         patch("routers.meta.meta_summary", new_callable=AsyncMock, return_value=dummy_meta):
        response = client.get("/api/v2/meta/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        # Strictly overwritten by V2 telemetry: False
        assert data["kill_switch_active"] is False
        assert data["equity"] == 100000.0


def test_v2_trading_status_telemetry(client):
    dummy_status = {"mode": "paper", "dhan_connected": False}
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=True), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=False), \
         patch("routers.trading_mode.get_trading_mode", new_callable=AsyncMock, return_value=dummy_status):
        response = client.get("/api/v2/trading/status")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is True
        assert data["kill_switch_active"] is False
        assert data["mode"] == "paper"


# ── Degraded / Disconnected DB State Tests ───────────────────────────────────

def test_v2_health_when_db_offline(client):
    with patch("routers.v2.ensure_connected", new_callable=AsyncMock, return_value=False), \
         patch("routers.v2.is_kill_switch_on", new_callable=AsyncMock, return_value=True):
        response = client.get("/api/v2/health")
        assert response.status_code == 200
        data = response.json()
        assert data["db_online"] is False
        assert data["kill_switch_active"] is True
        assert data["status"] == "degraded"
