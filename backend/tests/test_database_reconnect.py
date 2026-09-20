"""
Unit tests for database reconnection logic and ensure_connected().
Verifies mocked ping fail-then-success recreates the client and returns True.
"""
import os
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database


@pytest.mark.asyncio
async def test_ensure_connected_recreates_client_on_fail_then_success():
    """Mocked ping fail-then-success proves ensure_connected() recreates the client and returns True."""
    # 1. Existing handle fails ping
    mock_failing_db = MagicMock()
    mock_failing_db.command = AsyncMock(side_effect=Exception("Connection broken"))

    # 2. Recreated client succeeds ping
    mock_healthy_db = MagicMock()
    mock_healthy_db.command = AsyncMock(return_value={"ok": 1})

    with patch.object(database, "_db", mock_failing_db), \
         patch.object(database, "close_db", new_callable=AsyncMock) as mock_close, \
         patch.object(database, "connect_db", new_callable=AsyncMock, return_value=mock_healthy_db) as mock_connect:

        result = await database.ensure_connected()

        assert result is True
        mock_close.assert_awaited_once()
        mock_connect.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_connected_returns_false_if_recreate_fails():
    """If recreated connection also fails ping, ensure_connected returns False."""
    mock_failing_db = MagicMock()
    mock_failing_db.command = AsyncMock(side_effect=Exception("Persistent failure"))

    with patch.object(database, "_db", None), \
         patch.object(database, "close_db", new_callable=AsyncMock), \
         patch.object(database, "connect_db", new_callable=AsyncMock, return_value=None):

        result = await database.ensure_connected()
        assert result is False
