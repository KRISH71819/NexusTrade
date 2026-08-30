"""
Tests for:
1. Generator prompt builder (Section 3 + 5.2(e)): DO NOT REPEAT block & best near-miss
2. GET /api/research/status enriched candidates array (Section 2 + 5.2(b))
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import patch, AsyncMock
import pytest
from httpx import AsyncClient, ASGITransport

from alpha_generator import _build_prompt, _get_best_near_miss_line
from evolution_driver import get_run_status, _runs, _runs_guard


class TestGeneratorPromptBuilder:
    def test_prompt_contains_structural_rules(self):
        prompt = _build_prompt(3, [])
        assert "STRUCTURAL DNA PREFERENCES:" in prompt
        assert "cross-sectional ranking DNA" in prompt
        assert "HARD REJECTIONS" in prompt
        assert "turnover < 12x" in prompt

    def test_prompt_default_near_miss_when_memory_empty(self):
        prompt = _build_prompt(3, [])
        assert "PREVIOUS BEST NEAR-MISS" in prompt
        assert "Vol_Contraction_Breakout" in prompt
        assert "sharpe=0.85" in prompt
        assert "turnover=5.7x/yr" in prompt

    def test_prompt_picks_highest_sharpe_failed_alpha_from_memory(self):
        memory = [
            {
                "name": "Alpha_Low_Sharpe",
                "expression": "rank(close / sma(close, 50) - 1, 50)",
                "status": "rejected",
                "metrics": {"sharpe": 0.45, "ann_turnover": 8.2, "max_dd_pct": -35.0},
            },
            {
                "name": "Alpha_Best_Near_Miss",
                "expression": "rank(close / sma(close, 120) - 1, 120)",
                "status": "rejected",
                "metrics": {"sharpe": 0.92, "ann_turnover": 6.1, "max_dd_pct": -28.0},
            },
            {
                "name": "Alpha_Approved_Live",
                "expression": "close / sma(close, 200) - 1",
                "status": "approved",
                "metrics": {"sharpe": 1.20, "ann_turnover": 4.5, "max_dd_pct": -22.0},
            },
        ]
        near_miss_line = _get_best_near_miss_line(memory)
        assert "Alpha_Best_Near_Miss" in near_miss_line
        assert "0.92" in near_miss_line
        assert "6.1" in near_miss_line

        prompt = _build_prompt(3, memory)
        assert "Alpha_Best_Near_Miss" in prompt
        assert "DO NOT REPEAT THESE FAMILIES" in prompt
        assert "rank(close / sma(close, 50) - 1, 50)" in prompt

    def test_do_not_repeat_block_contains_failed_expressions(self):
        """Spec 5.2(e): when registry has failed alphas, prompt contains their expressions."""
        failed_alphas = [
            {
                "name": f"Failed_{i}",
                "expression": f"rank(delta(close, {60 + i * 20}), {100 + i * 10})",
                "status": "rejected",
                "metrics": {"sharpe": -0.1 * i, "max_dd_pct": -40.0 - i, "ann_turnover": 15.0 + i},
            }
            for i in range(1, 6)
        ]
        prompt = _build_prompt(3, failed_alphas)
        assert "DO NOT REPEAT THESE FAMILIES" in prompt
        for fa in failed_alphas:
            assert fa["expression"] in prompt


@pytest.mark.asyncio
async def test_research_status_returns_candidates_array_with_required_fields():
    """Spec 5.2(b): GET /api/research/status response includes candidates array with full schema."""
    from main import app

    mock_run_record = {
        "run_id": "test_run_12345",
        "status": "completed",
        "started_at": "2026-08-30T10:00:00+00:00",
        "started_ts": "2026-08-30T10:00:00+00:00",
        "started_ts_epoch": 1788084000.0,
        "finished_at": "2026-08-30T10:05:00+00:00",
        "count_requested": 2,
        "proposed": 2,
        "tested": 2,
        "passed": 0,
        "hof_promoted": 0,
        "hof_active": 0,
        "elapsed_s": 300.0,
        "error": None,
        "candidates": [
            {
                "name": "Vol_Contraction_Breakout",
                "hypothesis": "Low volatility squeeze precedes trending breakout",
                "expression": "rank(close / sma(close, 200) - 1, 200)",
                "metrics": {
                    "sharpe": 0.85,
                    "max_dd_pct": -42.31,
                    "ann_turnover": 5.7,
                    "fold_sharpes": [0.80, 0.90, 0.85],
                    "ann_return_pct": 18.5,
                    "ann_vol_pct": 16.2,
                },
                "gates": {
                    "sharpe": False,
                    "max_dd": False,
                    "stability": True,
                    "turnover": True,
                    "all": False,
                },
                "verdict": "APPROVE",
            },
            {
                "name": "Fast_Oscillator_Reject",
                "hypothesis": "Fast momentum",
                "expression": "macd_cross(12,26,9) > 0",
                "metrics": {
                    "sharpe": None,
                    "max_dd_pct": None,
                    "ann_turnover": None,
                    "fold_sharpes": [],
                    "ann_return_pct": None,
                    "ann_vol_pct": None,
                },
                "gates": {
                    "sharpe": False,
                    "max_dd": False,
                    "stability": False,
                    "turnover": False,
                    "all": False,
                },
                "verdict": "REJECT",
            },
        ],
        "log_tail": ["2026-08-30T10:05:00Z | batch completed"],
    }

    with _runs_guard:
        _runs["test_run_12345"] = mock_run_record

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/research/status?run_id=test_run_12345")

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "test_run_12345"
    assert data["status"] == "completed"
    assert "candidates" in data
    assert len(data["candidates"]) == 2

    # Check Candidate 1 fields
    c1 = data["candidates"][0]
    assert c1["name"] == "Vol_Contraction_Breakout"
    assert c1["hypothesis"] == "Low volatility squeeze precedes trending breakout"
    assert c1["expression"] == "rank(close / sma(close, 200) - 1, 200)"
    assert c1["verdict"] == "APPROVE"

    # Check metrics dict
    m1 = c1["metrics"]
    assert m1["sharpe"] == 0.85
    assert m1["max_dd_pct"] == -42.31
    assert m1["ann_turnover"] == 5.7
    assert m1["fold_sharpes"] == [0.80, 0.90, 0.85]
    assert m1["ann_return_pct"] == 18.5
    assert m1["ann_vol_pct"] == 16.2

    # Check gates dict
    g1 = c1["gates"]
    assert "sharpe" in g1 and "max_dd" in g1 and "stability" in g1 and "turnover" in g1 and "all" in g1

    # Check backward compatibility
    assert "log_tail" in data
    assert len(data["log_tail"]) >= 1
