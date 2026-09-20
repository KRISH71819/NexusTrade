"""Pure promotion-predicate tests for Phase 4. No DB needed."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alpha_sandbox.hall_of_fame import is_promotable


def _doc(sharpe_gate, stability, alpha_dd, bench_dd=None, all_gate=True, benchmark_clone=False):
    return {
        "gates": {"sharpe": sharpe_gate, "stability": stability, "all": all_gate},
        "metrics": {"max_dd_pct": alpha_dd, "benchmark_clone": benchmark_clone},
        "info": {"bench_max_dd_pct": bench_dd},
    }


class TestPromotion:
    def test_requires_sharpe_and_stability(self):
        assert not is_promotable(_doc(False, True, -10, -40), dd_mode="relative")
        assert not is_promotable(_doc(True, False, -10, -40), dd_mode="relative")

    def test_relative_dd_boundary(self):
        # bench -40, rel 0.75 -> threshold -30
        assert is_promotable(_doc(True, True, -30, -40), dd_mode="relative", dd_relative=0.75)
        assert not is_promotable(_doc(True, True, -31, -40), dd_mode="relative", dd_relative=0.75)

    def test_relative_requires_benchmark(self):
        assert not is_promotable(_doc(True, True, -10, None), dd_mode="relative")

    def test_absolute_mode(self):
        assert is_promotable(_doc(True, True, -20), dd_mode="absolute", abs_dd_pct=25)
        assert not is_promotable(_doc(True, True, -26), dd_mode="absolute", abs_dd_pct=25)

    def test_rejects_when_gates_all_is_false(self):
        assert not is_promotable(_doc(True, True, -10, -40, all_gate=False), dd_mode="relative")

    def test_rejects_benchmark_clone(self):
        assert not is_promotable(_doc(True, True, -10, -40, all_gate=True, benchmark_clone=True), dd_mode="relative")


import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId


@pytest.mark.asyncio
async def test_revalidate_hall_of_fame_demotes_invalid_entries():
    from routers.research import revalidate_hall_of_fame

    id1 = ObjectId()
    id2 = ObjectId()
    id3 = ObjectId()

    # Active members in HoF:
    # 1. Valid alpha
    # 2. Benchmark clone alpha
    # 3. Orphaned alpha (no registry doc)
    active_members = [
        {"_id": id1, "name": "alpha_valid", "status": "active"},
        {"_id": id2, "name": "alpha_clone", "status": "active"},
        {"_id": id3, "name": "alpha_orphan", "status": "active"},
    ]

    reg_docs = {
        "alpha_valid": {
            "name": "alpha_valid",
            "gates": {"sharpe": True, "stability": True, "all": True},
            "metrics": {"max_dd_pct": -20.0, "benchmark_clone": False},
            "info": {"bench_max_dd_pct": -40.0},
        },
        "alpha_clone": {
            "name": "alpha_clone",
            "gates": {"sharpe": True, "stability": True, "all": False},
            "metrics": {"max_dd_pct": -20.0, "benchmark_clone": True},
            "info": {"bench_max_dd_pct": -40.0},
        },
    }

    mock_hof_cursor = MagicMock()
    mock_hof_cursor.to_list = AsyncMock(return_value=active_members)

    mock_hof_coll = MagicMock()
    mock_hof_coll.find.return_value = mock_hof_cursor
    mock_hof_coll.update_one = AsyncMock()

    mock_reg_coll = MagicMock()
    async def mock_find_one(query):
        name = query.get("name")
        return reg_docs.get(name)
    mock_reg_coll.find_one = AsyncMock(side_effect=mock_find_one)

    mock_db = {
        "hall_of_fame": mock_hof_coll,
        "alpha_registry": mock_reg_coll,
    }

    with patch("routers.research.get_db", return_value=mock_db):
        res = await revalidate_hall_of_fame()

    assert res["status"] == "success"
    assert res["scanned"] == 3
    assert res["demoted"] == 2  # clone and orphan
    assert res["remaining_active"] == 1
    assert mock_hof_coll.update_one.call_count == 2

