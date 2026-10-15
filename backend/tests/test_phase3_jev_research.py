import asyncio
import pandas as pd
import numpy as np
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.jev_adapter import JevAdapter, MAX_QUESTIONS_PER_REQUEST
from core.template_bank import (generate_candidate_expressions,
                                jev_selection_questions, select_candidates)
from core.critics import check_structural_rejections, jev_critic_question, parse_critic_answer
from core.registry import (expression_fingerprint, find_duplicate, build_candidate_doc)
from core.incubator import evaluate_graduation, evaluate_demotion
from core.stress import stress_report, vol_spike_mask
from core.dsl import validate_expression

def _resp(status=200, answers=None, headers=None, usage=None):
    m = MagicMock()
    m.status_code = status
    m.headers = headers or {}
    m.json.return_value = {"answers": answers or {}, "usage": usage or {"input_tokens": 100, "output_tokens": 10}}
    m.text = ""
    return m

# ── Jev adapter ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unconfigured_returns_none():
    a = JevAdapter(api_key="")
    assert await a.query("state", {"q": {"type": "choice"}}) is None
    assert a.jev_online is False

@pytest.mark.asyncio
async def test_402_disables_and_returns_none():
    a = JevAdapter(api_key="k", backoff_start=0.01)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock,
               return_value=_resp(402, headers={"X-Tokens-Remaining": "0"})):
        assert await a.query("s", {"q": {"type": "choice"}}) is None
    assert a.disabled_reason == "quota_exhausted_402"
    assert a.last_tokens_remaining == 0

@pytest.mark.asyncio
async def test_401_disables():
    a = JevAdapter(api_key="k", backoff_start=0.01)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_resp(401)):
        assert await a.query("s", {"q": {"type": "choice"}}) is None
    assert a.disabled_reason == "bad_key_401"

@pytest.mark.asyncio
async def test_success_meters_input_tokens_only():
    a = JevAdapter(api_key="k")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock,
               return_value=_resp(200, answers={"q": {"choice": "A"}},
                                  headers={"X-Tokens-Remaining": "450000"},
                                  usage={"input_tokens": 120, "output_tokens": 999})):
        res = await a.query("s", {"q": {"type": "choice"}})
    assert res == {"q": {"choice": "A"}}
    assert a.total_input_tokens == 120          # output is free, not metered
    assert a.last_tokens_remaining == 450000
    assert a.meter()["estimated_spend_usd"] > 0

@pytest.mark.asyncio
async def test_429_retry_reuses_idempotency_key_without_blocking():
    a = JevAdapter(api_key="k", max_retries=2, backoff_start=0.01)
    mock_post = AsyncMock(side_effect=[_resp(429), _resp(200, answers={"q": {"score": 2}})])
    with patch("httpx.AsyncClient.post", mock_post):
        res = await asyncio.wait_for(a.query("s", {"q": {"type": "score"}}), timeout=5)
    assert res == {"q": {"score": 2}}
    keys = [c.kwargs["headers"]["Idempotency-Key"] for c in mock_post.call_args_list]
    assert keys[0] == keys[1]                    # same key => no double charge

@pytest.mark.asyncio
async def test_question_cap_enforced():
    a = JevAdapter(api_key="k")
    qs = {f"q{i}": {"type": "choice"} for i in range(MAX_QUESTIONS_PER_REQUEST + 1)}
    with pytest.raises(ValueError):
        await a.query("s", qs)

# ── Template bank ─────────────────────────────────────────────────────────────
def test_all_templates_valid_dsl():
    for c in generate_candidate_expressions():
        ok, err = validate_expression(c["expression"])
        assert ok, err

def test_select_candidates_bounded():
    ans = {"family": {"choice": "T_MOM_RANK"}, "lookback_band": {"score": 3}}
    cands = select_candidates(ans)
    assert cands and all(c["template_id"] == "T_MOM_RANK" for c in cands)
    assert select_candidates(None) == []          # Jev down => batch skips
    assert select_candidates({"family": {"choice": "NOPE"}}) == []

def test_selection_questions_shape():
    q = jev_selection_questions()
    assert set(q) == {"family", "lookback_band"}
    assert q["family"]["type"] == "choice" and q["lookback_band"]["type"] == "score"

# ── Critics ───────────────────────────────────────────────────────────────────
def test_structural_rejections():
    assert check_structural_rejections(
        "rsi(close, 14) < 30 and zscore(close, 10) < -1.5 and volume_ratio(volume, 10) > 1.5"
    )["verdict"] == "REJECT"
    assert check_structural_rejections("macd(close) > 0")["verdict"] == "REJECT"
    assert check_structural_rejections("rank(close, 60) * rank(volume, 60)")["verdict"] == "REVISE"
    assert check_structural_rejections("rank(close / sma(close, 200) - 1, 200) - 0.5") is None

def test_jev_critic_fail_closed():
    assert parse_critic_answer(None)["verdict"] == "REJECT"
    assert parse_critic_answer({"critic": {"choice": "maybe"}})["verdict"] == "REJECT"
    assert parse_critic_answer({"critic": {"choice": "APPROVE"}})["verdict"] == "APPROVE"
    assert jev_critic_question("close / sma(close, 200) - 1", "trend")["critic"]["type"] == "choice"

# ── Registry ──────────────────────────────────────────────────────────────────
def test_fingerprint_and_duplicate():
    fp1 = expression_fingerprint("Close / SMA(close, 200) - 1")
    fp2 = expression_fingerprint("close /  sma(close,200)  -1")
    assert fp1 == fp2
    known = [{"name": "old", "fingerprint": fp1, "returns_fingerprint": "zz"}]
    assert find_duplicate("close / sma(close, 200) - 1", pd.Series([0.0]), known) == "old"
    assert find_duplicate("close / sma(close, 60) - 1", pd.Series([0.0]), known) is None

def test_final_verdict_rules():
    gates = {"sharpe": True, "max_dd": True, "stability": True, "turnover": True,
             "clone": True, "universe": True, "all": True}
    doc = build_candidate_doc("a", "close / sma(close, 200) - 1", "h", "template",
                              {}, [], gates, {"verdict": "APPROVE"})
    assert doc["final_verdict"] == "PASS_GATES"
    doc2 = build_candidate_doc("b", "close / sma(close, 200) - 1", "h", "template",
                               {}, [], gates, {"verdict": "APPROVE"}, duplicate_of="a")
    assert doc2["final_verdict"] == "REJECT" and doc2["duplicate_of"] == "a"

# ── Incubator ─────────────────────────────────────────────────────────────────
def test_graduation_and_demotion():
    dates = pd.date_range("2026-01-01", periods=40, freq="B")
    good = pd.Series([0.001, 0.003] * 20, index=dates)       # varying => real vol
    sleeve_m = pd.Series([0.003, -0.001] * 20, index=dates)  # opposite phase => corr ~ -1
    clone = good * 1.05
    assert evaluate_graduation(good, {"M": sleeve_m})["graduated"] is True
    assert evaluate_graduation(good, {"M": clone})["graduated"] is False
    assert evaluate_graduation(good.iloc[:10], {})["graduated"] is False
    assert evaluate_demotion(0.9) == ("FULL_CAPITAL", "")
    act, reason = evaluate_demotion(0.3)
    assert act == "HALVE_CAPITAL" and reason
    act, reason = evaluate_demotion(-0.1)
    assert act == "DEMOTE" and reason

# ── Stress ────────────────────────────────────────────────────────────────────
def test_stress_windows():
    idx = pd.date_range("2020-01-01", periods=200, freq="B")
    net = pd.Series(np.random.RandomState(7).normal(-0.002, 0.03, 200), index=idx)
    rep = stress_report(net)
    assert rep["covid_2020"]["days"] > 20 and rep["covid_2020"]["sharpe"] is not None
    assert rep["gfc_2008"]["sharpe"] is None      # no 2008 data in this series
    ir = pd.Series(np.random.RandomState(3).normal(0, 0.01, 200), index=idx)
    ir.loc[idx[100:120]] = 0.05
    mask = vol_spike_mask(ir, lookback=150, pctile=90)
    assert mask.sum() > 0
    rep2 = stress_report(net, ir)
    assert rep2["vol_spike"]["days"] > 0
