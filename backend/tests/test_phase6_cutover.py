"""
Phase 6 Cutover & Migration Unit Tests
Verifies:
1. Standing Rule #8: 'ohlcv_history' is NEVER renamed, modified, or dropped.
2. V1 collection archival (renaming to 'legacy_v1_*') and idempotency.
3. Dry-run mode safety (no database mutations).
4. V2 collection initialization and index creation.
5. Dhan live execution safety gates (mode, kill switch, book status, market hours, creds, order sanity).
"""

import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

# Ensure both backend/ and repo root are in python path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from tools.migrate_v1_to_v2 import run_migration, SACROSANCT_COLLECTIONS
from core.dhan_gate import (
    can_execute_live_order,
    is_market_open,
    validate_dhan_credentials,
    DhanGateResult,
)
from core.ist import IST


# ─────────────────────────────────────────────────────────────────────────────
# 1. Migration & Archival Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ohlcv_history_never_dropped_or_renamed():
    """Standing Rule #8: ohlcv_history must be sacrosanct and never renamed/dropped."""
    mock_db = MagicMock()
    mock_db.list_collection_names = AsyncMock(return_value=["ohlcv_history", "trades", "portfolio"])
    
    mock_ohlcv = MagicMock()
    mock_ohlcv.rename = AsyncMock()
    mock_ohlcv.drop = AsyncMock()
    
    mock_trades = MagicMock()
    mock_trades.rename = AsyncMock()
    
    mock_portfolio = MagicMock()
    mock_portfolio.rename = AsyncMock()

    def getitem(name):
        if name == "ohlcv_history":
            return mock_ohlcv
        if name == "trades":
            return mock_trades
        if name == "portfolio":
            return mock_portfolio
        col = MagicMock()
        col.create_index = AsyncMock()
        return col

    mock_db.__getitem__.side_effect = getitem

    summary = await run_migration(mock_db, dry_run=False)

    # ohlcv_history was preserved
    assert "ohlcv_history" in summary["sacrosanct_preserved"]
    mock_ohlcv.rename.assert_not_called()
    mock_ohlcv.drop.assert_not_called()

    # other legacy collections were renamed
    mock_trades.rename.assert_called_once_with("legacy_v1_trades")
    mock_portfolio.rename.assert_called_once_with("legacy_v1_portfolio")


@pytest.mark.asyncio
async def test_migration_dry_run_does_not_mutate():
    """Dry-run must not call rename or create_index."""
    mock_db = MagicMock()
    mock_db.list_collection_names = AsyncMock(return_value=["trades", "portfolio", "ohlcv_history"])
    
    mock_trades = MagicMock()
    mock_trades.rename = AsyncMock()
    mock_db.__getitem__.return_value = mock_trades

    summary = await run_migration(mock_db, dry_run=True)

    assert summary["dry_run"] is True
    # In dry-run, rename is never called
    mock_trades.rename.assert_not_called()
    mock_trades.create_index.assert_not_called()
    
    # But actions are previewed in summary
    assert len(summary["renamed"]) == 2
    assert summary["renamed"][0]["executed"] is False


@pytest.mark.asyncio
async def test_migration_idempotent_when_target_already_exists():
    """If legacy_v1_* already exists, skip renaming to avoid clobbering."""
    mock_db = MagicMock()
    mock_db.list_collection_names = AsyncMock(return_value=["trades", "legacy_v1_trades"])
    
    mock_trades = MagicMock()
    mock_trades.rename = AsyncMock()
    mock_trades.create_index = AsyncMock()
    mock_db.__getitem__.return_value = mock_trades

    summary = await run_migration(mock_db, dry_run=False)

    assert ("trades", "legacy_v1_trades") in summary["skipped_existing_archive"]
    mock_trades.rename.assert_not_called()


@pytest.mark.asyncio
async def test_migration_initializes_v2_indexes():
    """V2 collections must have their indexes initialized on live run."""
    mock_db = MagicMock()
    mock_db.list_collection_names = AsyncMock(return_value=[])
    
    index_calls = {}

    def getitem(name):
        col = MagicMock()
        col.create_index = AsyncMock(side_effect=lambda keys, unique=False: index_calls.setdefault(name, []).append((keys, unique)))
        return col

    mock_db.__getitem__.side_effect = getitem

    summary = await run_migration(mock_db, dry_run=False)

    assert len(summary["v2_collections_initialized"]) == 5
    assert "eod_book" in index_calls
    assert "eod_snapshots" in index_calls
    assert "alpha_hof" in index_calls
    assert "research_runs" in index_calls
    assert "audit_log" in index_calls


# ─────────────────────────────────────────────────────────────────────────────
# 2. Dhan Safety Gate Tests
# ─────────────────────────────────────────────────────────────────────────────

def _valid_order():
    return {
        "ticker": "RELIANCE.NS",
        "quantity": 10,
        "price": 2850.50,
        "side": "BUY",
    }


def _valid_credentials():
    return {
        "client_id": "1100123456",
        "pin": "123456",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "access_token": "valid_mock_token_xyz",
    }


def _market_open_time():
    # Wednesday 11:30 AM IST
    return datetime(2026, 10, 14, 11, 30, tzinfo=IST)


def _market_closed_weekend():
    # Sunday 11:30 AM IST
    return datetime(2026, 10, 18, 11, 30, tzinfo=IST)


def _market_closed_evening():
    # Wednesday 19:30 PM IST (7:30 PM)
    return datetime(2026, 10, 14, 19, 30, tzinfo=IST)


def test_dhan_gate_rejects_paper_mode():
    res = can_execute_live_order(
        trading_mode="paper",
        kill_switch_on=False,
        book_status="OPEN",
        credentials=_valid_credentials(),
        order=_valid_order(),
        now=_market_open_time(),
    )
    assert res.allowed is False
    assert res.gate_failed == "MODE_GATE"


def test_dhan_gate_rejects_kill_switch_active():
    res = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=True,
        book_status="OPEN",
        credentials=_valid_credentials(),
        order=_valid_order(),
        now=_market_open_time(),
    )
    assert res.allowed is False
    assert res.gate_failed == "KILL_SWITCH_GATE"


def test_dhan_gate_rejects_halted_book():
    res = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=False,
        book_status="HALTED",
        credentials=_valid_credentials(),
        order=_valid_order(),
        now=_market_open_time(),
    )
    assert res.allowed is False
    assert res.gate_failed == "BOOK_STATUS_GATE"


def test_dhan_gate_rejects_weekend():
    res = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=False,
        book_status="OPEN",
        credentials=_valid_credentials(),
        order=_valid_order(),
        now=_market_closed_weekend(),
    )
    assert res.allowed is False
    assert res.gate_failed == "MARKET_HOURS_GATE"


def test_dhan_gate_rejects_outside_market_hours():
    res = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=False,
        book_status="OPEN",
        credentials=_valid_credentials(),
        order=_valid_order(),
        now=_market_closed_evening(),
    )
    assert res.allowed is False
    assert res.gate_failed == "MARKET_HOURS_GATE"


def test_dhan_gate_rejects_missing_credentials():
    res = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=False,
        book_status="OPEN",
        credentials={"client_id": ""},
        order=_valid_order(),
        now=_market_open_time(),
    )
    assert res.allowed is False
    assert res.gate_failed == "CREDENTIAL_GATE"


def test_dhan_gate_rejects_invalid_order_sanity():
    # Zero quantity
    bad_order = {"ticker": "INFY.NS", "quantity": 0, "price": 1800.0, "side": "BUY"}
    res = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=False,
        book_status="OPEN",
        credentials=_valid_credentials(),
        order=bad_order,
        now=_market_open_time(),
    )
    assert res.allowed is False
    assert res.gate_failed == "ORDER_SANITY_GATE"

    # Negative price
    bad_price_order = {"ticker": "INFY.NS", "quantity": 10, "price": -50.0, "side": "BUY"}
    res2 = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=False,
        book_status="OPEN",
        credentials=_valid_credentials(),
        order=bad_price_order,
        now=_market_open_time(),
    )
    assert res2.allowed is False
    assert res2.gate_failed == "ORDER_SANITY_GATE"

    # Invalid side
    bad_side_order = {"ticker": "INFY.NS", "quantity": 10, "price": 1800.0, "side": "HOLD"}
    res3 = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=False,
        book_status="OPEN",
        credentials=_valid_credentials(),
        order=bad_side_order,
        now=_market_open_time(),
    )
    assert res3.allowed is False
    assert res3.gate_failed == "ORDER_SANITY_GATE"


def test_dhan_gate_all_pass():
    res = can_execute_live_order(
        trading_mode="live",
        kill_switch_on=False,
        book_status="OPEN",
        credentials=_valid_credentials(),
        order=_valid_order(),
        now=_market_open_time(),
    )
    assert res.allowed is True
    assert res.gate_failed is None
    assert "passed" in res.reason.lower()
