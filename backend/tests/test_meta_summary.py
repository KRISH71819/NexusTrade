"""
Regression tests for GET /api/meta/summary and its pure helpers.
Verifies Section 1 requirements:
- Real Mongo meta doc shape handling (missing date in equity docs, datetime timestamps)
- Helpers degrade gracefully on exceptions and endpoint always returns 200
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
import pytest
from httpx import AsyncClient, ASGITransport

from routers.meta import (
    replay_realized_pnl,
    next_rebalance_info,
    pnl_vs_history,
    _extract_date_str,
)


# Real MongoDB meta document shape as captured from live database
REAL_META_DOC = {
    "_id": "meta",
    "cash": 267065.50,
    "holdings": [
        {"ticker": "TITAN.NS", "quantity": 6, "avg_price": 5076.42, "current_price": 5169.2, "market_value": 31015.2},
        {"ticker": "NAUKRI.NS", "quantity": 22, "avg_price": 1348.37, "current_price": 1354.0, "market_value": 29788.0},
        {"ticker": "AETHER.NS", "quantity": 19, "avg_price": 1631.6, "current_price": 1696.8, "market_value": 32239.2},
    ],
    "total_value": 1004465.03,
    "peak_value": 1000000.0,
    "last_rebalance": "2026-08-18",
    "created_at": datetime(2026, 8, 15, 17, 53, 34, tzinfo=timezone.utc),
    "holdings_value": 737399.53,
    "strat_info": {
        "signal": "close / sma(close, 200) - 1",
        "trend_on": True,
        "realized_vol": 0.1857,
        "vol_scale": 0.808,
        "exposure": 0.808,
        "top_n": 25,
        "rebalance_days": 60,
    },
    "reconciliation": {
        "target_exposure": 0.808,
        "actual_exposure": 0.731,
        "names_target": 25,
        "names_held": 24,
        "orders": 0,
    },
}

# Live Mongo equity docs shape (timestamp is datetime, no date field)
REAL_EQUITY_DOCS = [
    {"timestamp": datetime(2026, 8, 15, 17, 59, 1, tzinfo=timezone.utc), "total_value": 994643.59},
    {"timestamp": datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc), "total_value": 1001000.00},
    {"timestamp": datetime(2026, 8, 28, 10, 2, 0, tzinfo=timezone.utc), "total_value": 1004465.03},
]

# Live Mongo trades shape
REAL_TRADES = [
    {
        "timestamp": datetime(2026, 8, 15, 17, 58, 45, tzinfo=timezone.utc),
        "ticker": "TITAN.NS",
        "action": "BUY",
        "quantity": 19,
        "price": 5076.42,
        "charges": {"stt": 96.45, "total_charges": 138.54},
    },
    {
        "timestamp": datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc),
        "ticker": "TITAN.NS",
        "action": "SELL",
        "quantity": 13,
        "price": 5150.00,
        "charges": {"total_charges": 95.20},
    },
]


def _mock_coll(find_result=None, find_one_result=None):
    from unittest.mock import MagicMock
    coll = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=find_result or [])
    coll.find = MagicMock(return_value=cursor)
    coll.find_one = AsyncMock(return_value=find_one_result)
    return coll


class TestMetaHelpers:
    def test_extract_date_str_various_types(self):
        assert _extract_date_str({"date": "2026-08-20"}) == "2026-08-20"
        assert _extract_date_str({"timestamp": datetime(2026, 8, 25, 12, 0)}) == "2026-08-25"
        assert _extract_date_str({"timestamp": "2026-08-25T12:00:00+00:00"}) == "2026-08-25"
        assert _extract_date_str({}) == ""
        assert _extract_date_str(None) == ""

    def test_pnl_vs_history_with_real_equity_docs(self):
        pnl = pnl_vs_history(REAL_EQUITY_DOCS, 1004465.03)
        assert "daily_pnl" in pnl
        assert "weekly_pnl" in pnl
        assert pnl["daily_pnl"] == pytest.approx(1004465.03 - 1001000.00, abs=0.01)

    def test_pnl_vs_history_empty_and_corrupt(self):
        assert pnl_vs_history([], 1000000.0) == {"daily_pnl": 0.0, "daily_pnl_pct": 0.0, "weekly_pnl": 0.0, "weekly_pnl_pct": 0.0}
        corrupt = [{"timestamp": None, "total_value": None}]
        res = pnl_vs_history(corrupt, 1000000.0)
        assert isinstance(res, dict)

    def test_next_rebalance_info_parses_cleanly(self):
        info = next_rebalance_info("2026-08-18", 60, "2026-08-30")
        assert info["next_rebalance_date"] is not None
        assert info["days_until_rebalance"] is not None
        assert info["days_until_rebalance"] > 0

    def test_next_rebalance_info_handles_invalid_date(self):
        info = next_rebalance_info("invalid-date", 60, "2026-08-30")
        assert info["next_rebalance_date"] is None
        assert info["days_until_rebalance"] is None

    def test_replay_realized_pnl_with_real_trades(self):
        realized = replay_realized_pnl(REAL_TRADES)
        assert isinstance(realized, float)


@pytest.mark.asyncio
async def test_meta_summary_returns_200_with_real_doc():
    """Verify GET /api/meta/summary returns 200 and correct fields with live Mongo doc shape."""
    from main import app

    meta_coll = _mock_coll(find_one_result=REAL_META_DOC)
    trades_coll = _mock_coll(find_result=REAL_TRADES)
    equity_coll = _mock_coll(find_result=REAL_EQUITY_DOCS)

    with patch("routers.meta.get_meta_portfolio_collection", return_value=meta_coll), \
         patch("routers.meta.get_meta_trades_collection", return_value=trades_coll), \
         patch("routers.meta.get_meta_equity_collection", return_value=equity_coll), \
         patch("kill_switch.is_kill_switch_on", new_callable=AsyncMock, return_value=False):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/meta/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total_value"] == pytest.approx(1004465.03, abs=0.01)
        assert data["holdings_count"] == 3
        assert data["exposure_actual"] == pytest.approx(93042.4 / 1004465.03, abs=0.001)
        assert "daily_pnl" in data
        assert "weekly_pnl" in data
        assert "next_rebalance_date" in data


@pytest.mark.asyncio
async def test_meta_summary_graceful_degradation_on_helper_failure():
    """Section 1 requirement: endpoint must return 200 even when a helper raises an exception."""
    from main import app

    meta_coll = _mock_coll(find_one_result=REAL_META_DOC)
    trades_coll = _mock_coll(find_result=REAL_TRADES)
    equity_coll = _mock_coll(find_result=REAL_EQUITY_DOCS)

    with patch("routers.meta.get_meta_portfolio_collection", return_value=meta_coll), \
         patch("routers.meta.get_meta_trades_collection", return_value=trades_coll), \
         patch("routers.meta.get_meta_equity_collection", return_value=equity_coll), \
         patch("routers.meta.pnl_vs_history", side_effect=ValueError("Simulated equity corruption")), \
         patch("routers.meta.replay_realized_pnl", side_effect=RuntimeError("Simulated ledger error")), \
         patch("routers.meta.next_rebalance_info", side_effect=Exception("Simulated cadence error")), \
         patch("kill_switch.is_kill_switch_on", new_callable=AsyncMock, return_value=False):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/meta/summary")

        # Must still return 200, NOT 500
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total_value"] == pytest.approx(1004465.03, abs=0.01)
        assert data["realized_pnl"] is None
        assert data["next_rebalance_date"] is None
