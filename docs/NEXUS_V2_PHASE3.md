# NexusTrade v2 — PHASE 3 (Jev adapter, template bank, critics, registry, incubator, stress)
Branch: v2. ADDITIVE ONLY into backend/core/ and backend/tests/.
No Mongo writes in this phase (registry persistence lands with Phase 4 scheduler).
Verify: cd backend && python -m pytest tests -q  (must stay 100% green)
Frozen rules honoured: one AI only (Jev); Jev answers typed questions only;
Jev down (None) => research batch skips, book runs code-only; batch all questions
for one state in ONE request; retry 429/502 with SAME Idempotency-Key; meter
input tokens only; 402/401 => jev_online False, no crash.

===FILE: backend/core/jev_adapter.py===
"""Jev SystemOne bounded-decision adapter (v2 Phase 3).
The only AI in the system. It answers choice/score/noul questions, nothing else.
Wire: POST https://jevmodel.org/v1/systemone, Bearer $JEVMODEL_API_KEY,
body {model, state<=8000 chars, questions<=8}. Output tokens are free;
only usage.input_tokens costs money ($0.042 / 1M input)."""
import asyncio
import os
import uuid
import logging
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)

JEV_ENDPOINT = "https://jevmodel.org/v1/systemone"
DEFAULT_MODEL = "jev-latest"   # pin to a fixed version once thresholds are tuned
MAX_QUESTIONS_PER_REQUEST = 8
MAX_STATE_CHARS = 8000
PRICE_PER_1M_INPUT = 0.042
HARD_MONTHLY_CAP_USD = 2.0

class JevAdapter:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 30.0,
                 max_retries: int = 3, backoff_start: float = 1.0):
        self.api_key = api_key if api_key is not None else os.environ.get("JEVMODEL_API_KEY", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff_start
        self.last_tokens_remaining: Optional[int] = None
        self.total_input_tokens = 0
        self.calls = 0
        self.failures = 0
        self.disabled_reason: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    @property
    def jev_online(self) -> bool:
        return self.is_configured and self.disabled_reason is None

    def meter(self) -> dict:
        return {
            "jev_online": self.jev_online,
            "configured": self.is_configured,
            "disabled_reason": self.disabled_reason,
            "calls": self.calls,
            "failures": self.failures,
            "total_input_tokens": self.total_input_tokens,
            "last_tokens_remaining": self.last_tokens_remaining,
            "estimated_spend_usd": round(self.total_input_tokens / 1e6 * PRICE_PER_1M_INPUT, 6),
            "monthly_cap_usd": HARD_MONTHLY_CAP_USD,
        }

    async def query(self, state: str, questions: Dict[str, Dict[str, Any]],
                    model: str = DEFAULT_MODEL) -> Optional[Dict[str, Any]]:
        """Answers dict on success. None when Jev is down/disabled — callers
        MUST treat None as 'skip the AI step' (fail-closed)."""
        if not self.is_configured:
            self.disabled_reason = self.disabled_reason or "no_api_key"
            return None
        if self.disabled_reason:
            return None
        if not questions or len(questions) > MAX_QUESTIONS_PER_REQUEST:
            raise ValueError(f"questions must be 1..{MAX_QUESTIONS_PER_REQUEST}")
        payload = {"model": model, "state": state[:MAX_STATE_CHARS], "questions": questions}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        }
        backoff = self.backoff
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = await client.post(JEV_ENDPOINT, json=payload, headers=headers)
                except httpx.RequestError as exc:
                    self.failures += 1
                    if attempt == self.max_retries:
                        logger.warning(f"Jev unreachable: {exc}")
                        return None
                    await asyncio.sleep(backoff); backoff *= 2; continue
                rem = resp.headers.get("X-Tokens-Remaining")
                if rem is not None:
                    try:
                        self.last_tokens_remaining = int(rem)
                    except ValueError:
                        pass
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage") or {}
                    self.total_input_tokens += int(usage.get("input_tokens", 0) or 0)
                    self.calls += 1
                    return data.get("answers") or {}
                if resp.status_code in (429, 502):
                    self.failures += 1
                    if attempt == self.max_retries:
                        logger.warning(f"Jev {resp.status_code} after {attempt} attempts")
                        return None
                    await asyncio.sleep(backoff); backoff *= 2; continue  # same Idempotency-Key
                if resp.status_code == 402:
                    self.disabled_reason = "quota_exhausted_402"
                    logger.warning("Jev quota exhausted (402) — code-only mode")
                    return None
                if resp.status_code == 401:
                    self.disabled_reason = "bad_key_401"
                    logger.error("Jev key rejected (401) — code-only mode")
                    return None
                self.failures += 1
                logger.warning(f"Jev unexpected {resp.status_code}: {resp.text[:150]}")
                return None
        return None
===END===

===FILE: backend/core/template_bank.py===
"""Bounded template bank — the ONLY source of candidate formulas (D2).
Jev never writes DSL: it picks a family (choice) and a lookback band (score);
code expands that bounded selection into validate_expression-checked DSL."""
from core.dsl import validate_expression

ALPHA_TEMPLATES = [
    {"id": "T_MOM_RANK", "name": "Cross-Sectional Momentum", "family": "momentum",
     "template": "close / sma(close, {window}) - 1", "params": {"window": [60, 120, 200]}},
    {"id": "T_LOW_VOL", "name": "Defensive Low Volatility", "family": "defensive",
     "template": "-(std(close, {window}) / close)", "params": {"window": [60, 90, 120]}},
    {"id": "T_REV", "name": "Mean Reversion Satellite", "family": "reversion",
     "template": "-(close / sma(close, {window}) - 1)", "params": {"window": [14, 21, 30]}},
    {"id": "T_VOL_MOM", "name": "Volume-Confirmed Momentum", "family": "volume_momentum",
     "template": "(close / sma(close, {window}) - 1) * volume_ratio(volume, {vol_window})",
     "params": {"window": [40, 60], "vol_window": [20, 30]}},
]
BANDS = ["very_fast", "fast", "medium", "slow", "very_slow"]

def _expand_one(tmpl, window, vol_window=None):
    expr = tmpl["template"].format(window=window, vol_window=vol_window) if vol_window \
        else tmpl["template"].format(window=window)
    ok, _ = validate_expression(expr)
    if not ok:
        return None
    params = {"window": window}
    if vol_window:
        params["vol_window"] = vol_window
    return {"template_id": tmpl["id"], "family": tmpl["family"],
            "expression": expr, "params": params}

def generate_candidate_expressions() -> list:
    out = []
    for t in ALPHA_TEMPLATES:
        if t["id"] == "T_VOL_MOM":
            for w in t["params"]["window"]:
                for vw in t["params"]["vol_window"]:
                    c = _expand_one(t, w, vw)
                    if c:
                        out.append(c)
        else:
            for w in t["params"]["window"]:
                c = _expand_one(t, w)
                if c:
                    out.append(c)
    return out

def jev_selection_questions() -> dict:
    """Bounded generator questions — send in ONE batched request."""
    return {
        "family": {
            "type": "choice",
            "instructions": "Given the market state, which alpha family should the next research batch explore?",
            "criteria": {t["id"]: t["name"] for t in ALPHA_TEMPLATES},
        },
        "lookback_band": {
            "type": "score",
            "instructions": "How slow should the lookback be? 1=very_fast .. 5=very_slow",
            "criteria": BANDS,
        },
    }

def select_candidates(answers, max_candidates: int = 6) -> list:
    """Expand a Jev selection. None / unknown answer => [] (batch skips)."""
    if not answers:
        return []
    fam = (answers.get("family") or {}).get("choice")
    tmpl = next((t for t in ALPHA_TEMPLATES if t["id"] == fam), None)
    if tmpl is None:
        return []
    raw = (answers.get("lookback_band") or {}).get("score")
    try:
        band = int(round(float(raw)))
    except (TypeError, ValueError):
        band = 3
    band = max(1, min(5, band))
    out = []
    if tmpl["id"] == "T_VOL_MOM":
        windows = tmpl["params"]["window"]
        vws = tmpl["params"]["vol_window"]
        w = windows[max(0, min(len(windows) - 1, band - 3))]
        for vw in vws:
            c = _expand_one(tmpl, w, vw)
            if c:
                out.append(c)
    else:
        windows = tmpl["params"]["window"]
        idx = max(0, min(len(windows) - 1, band - 3 + (1 if band > 3 else 0)))
        idx = max(0, min(len(windows) - 1, band - 2))
        c = _expand_one(tmpl, windows[idx])
        if c:
            out.append(c)
        for w in windows:
            if len(out) >= 2:
                break
            extra = _expand_one(tmpl, w)
            if extra and extra not in out:
                out.append(extra)
    return out[:max_candidates]
===END===

===FILE: backend/core/critics.py===
"""Structural critic (code, ported logic) + Jev adversarial critic (bounded choice).
Order: structural check FIRST (free), then Jev. Any failure => fail-closed."""
import ast
import re
from typing import Optional

def check_structural_rejections(expr: str) -> Optional[dict]:
    if not expr or not isinstance(expr, str):
        return None
    tree = None
    try:
        tree = ast.parse(expr, mode="eval")
    except Exception:
        pass
    if tree is not None:
        compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
        if len(compares) >= 3:
            return {"verdict": "REJECT",
                    "reasons": ["selectivity risk: 3+ conjunctions never fire"],
                    "fatal_flaw": "selectivity risk: 3+ conjunctions never fire"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id.lower() in ("macd", "macd_hist"):
                    return {"verdict": "REJECT",
                            "reasons": ["structural turnover too high — fast oscillator"],
                            "fatal_flaw": "structural turnover too high — fast oscillator"}
            for arg in getattr(node, "args", []):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                    if 0 < arg.value < 60:
                        return {"verdict": "REJECT",
                                "reasons": ["structural turnover too high — fast oscillator"],
                                "fatal_flaw": "structural turnover too high — fast oscillator"}
    has_threshold = bool(re.search(r'[><=!]=?', expr)) if tree is None else \
        any(isinstance(n, ast.Compare) for n in ast.walk(tree))
    if not has_threshold:
        if re.search(r'\b(rank|abs|std|volume_ratio)\b', expr, re.I) and \
           not re.search(r'-\s*(?:0\.5|0\.50|1\.0|1\b)', expr):
            return {"verdict": "REVISE",
                    "reasons": ["no cross-sectional selection: non-negative ops without centering or threshold"],
                    "fatal_flaw": "no cross-sectional selection"}
    return None

def jev_critic_question(expr: str, hypothesis: str) -> dict:
    state_note = f"EXPRESSION: {expr[:300]} HYPOTHESIS: {hypothesis[:300]}"
    return {"critic": {
        "type": "choice",
        "instructions": ("You are a hostile quant reviewer for NSE daily-bar alphas. "
                         "Flag overfitting, churn vs ~1.1% round-trip cost, degenerate "
                         f"exposure, economic nonsense. {state_note}"),
        "criteria": {
            "APPROVE": "no fatal flaw",
            "REVISE": "fixable with one small change",
            "REJECT": "structurally wrong",
        },
    }}

def parse_critic_answer(answers) -> dict:
    """Fail-closed: Jev down or garbage => REJECT."""
    if not answers:
        return {"verdict": "REJECT", "reasons": ["jev unavailable (fail-closed)"], "fatal_flaw": ""}
    verdict = str((answers.get("critic") or {}).get("choice", "")).upper()
    if verdict not in ("APPROVE", "REVISE", "REJECT"):
        return {"verdict": "REJECT", "reasons": ["unparseable critic answer (fail-closed)"], "fatal_flaw": ""}
    return {"verdict": verdict, "reasons": [], "fatal_flaw": ""}
===END===

===FILE: backend/core/registry.py===
"""Candidate registry pure core (v2 Phase 3). Mongo persistence arrives with the
Phase-4 scheduler; collection name frozen here (_v2 dev suffix rule)."""
import hashlib
import re
from datetime import datetime, timezone

COLLECTION = "alpha_registry_v2"

def normalize_expression(expr: str) -> str:
    # DSL tokens never contain spaces; strip ALL whitespace so cosmetic
    # spacing variants fingerprint identically (duplicate detection).
    return re.sub(r'\s+', '', str(expr).strip().lower())

def expression_fingerprint(expr: str) -> str:
    return hashlib.sha256(normalize_expression(expr).encode()).hexdigest()[:16]

def returns_fingerprint(daily_net) -> str:
    vals = [round(float(v), 6) for v in list(daily_net)[:4000]]
    return hashlib.sha256(",".join(map(str, vals)).encode()).hexdigest()[:16]

def find_duplicate(expr: str, daily_net, known: list) -> str | None:
    """known = list of docs with 'fingerprint' and 'returns_fingerprint'."""
    fp = expression_fingerprint(expr)
    rfp = returns_fingerprint(daily_net)
    for doc in known:
        if doc.get("fingerprint") == fp or doc.get("returns_fingerprint") == rfp:
            return doc.get("name") or doc.get("fingerprint")
    return None

def build_candidate_doc(name, expr, hypothesis, source, metrics, folds, gates,
                        critic, duplicate_of=None, reject_reason=None,
                        returns_fp=None) -> dict:
    dup = bool(duplicate_of)
    final = "PASS_GATES" if (gates.get("all") and not dup and not reject_reason) else "REJECT"
    return {
        "name": name,
        "expression": expr,
        "hypothesis": hypothesis,
        "source": source,
        "created_at": datetime.now(timezone.utc),
        "fingerprint": expression_fingerprint(expr),
        "returns_fingerprint": returns_fp,
        "duplicate_of": duplicate_of,
        "critic": critic,
        "metrics": metrics,
        "fold_sharpes": folds,
        "gates": gates,
        "reject_reason": reject_reason or (critic or {}).get("fatal_flaw") or None,
        "final_verdict": final,
    }
===END===

===FILE: backend/core/incubator.py===
"""Shadow incubator + demotion ladder (v2 Phase 3, pure)."""
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

GRADUATION_MIN_SHARPE = 0.8
GRADUATION_MAX_CORRELATION = 0.8
DEMOTION_HALVE_SHARPE = 0.5
DEMOTION_EXIT_SHARPE = 0.0
MIN_INCUBATION_DAYS = 30   # ~6 trading weeks

def evaluate_graduation(candidate_returns: pd.Series,
                        existing_sleeve_returns: Dict[str, pd.Series],
                        min_sharpe: float = GRADUATION_MIN_SHARPE,
                        max_corr: float = GRADUATION_MAX_CORRELATION) -> Dict[str, Any]:
    d = candidate_returns.dropna()
    if len(d) < MIN_INCUBATION_DAYS:
        return {"graduated": False, "reason": "insufficient_incubation_period"}
    ann_ret = float(d.mean()) * 252
    ann_vol = float(d.std(ddof=1)) * np.sqrt(252)
    sharpe = (ann_ret / ann_vol) if ann_vol > 0 else 0.0
    if sharpe < min_sharpe:
        return {"graduated": False, "sharpe": round(sharpe, 2),
                "reason": f"sharpe {sharpe:.2f} < {min_sharpe}"}
    correlations = {}
    for sleeve, s_ret in existing_sleeve_returns.items():
        combined = pd.concat([d, s_ret.dropna()], axis=1).dropna()
        if len(combined) >= 20:
            corr = float(combined.iloc[:, 0].corr(combined.iloc[:, 1]))
            correlations[sleeve] = round(corr, 2)
            if corr >= max_corr:
                return {"graduated": False, "sharpe": round(sharpe, 2),
                        "correlations": correlations,
                        "reason": f"correlation with {sleeve} ({corr:.2f}) >= {max_corr}"}
    return {"graduated": True, "sharpe": round(sharpe, 2),
            "correlations": correlations, "reason": "passed_all_gates"}

def evaluate_demotion(trailing_6m_sharpe: float) -> tuple:
    """Returns (action, reason). Frozen ladder: >=0.5 full, [0,0.5) halve, <0 demote."""
    if trailing_6m_sharpe >= DEMOTION_HALVE_SHARPE:
        return "FULL_CAPITAL", ""
    if trailing_6m_sharpe >= DEMOTION_EXIT_SHARPE:
        return "HALVE_CAPITAL", f"rolling 6m sharpe {trailing_6m_sharpe:.2f} < 0.5"
    return "DEMOTE", f"rolling 6m sharpe {trailing_6m_sharpe:.2f} < 0"
===END===

===FILE: backend/core/stress.py===
"""Stress windows (frozen: 2008 GFC, 2020 COVID, vol-spike) for every candidate."""
import numpy as np
import pandas as pd
from typing import Optional

WINDOWS = {
    "gfc_2008": ("2008-01-01", "2009-03-31"),
    "covid_2020": ("2020-02-15", "2020-06-30"),
}

def _window_stats(seg: pd.Series) -> dict:
    seg = seg.dropna()
    if len(seg) < 20:
        return {"days": int(len(seg)), "sharpe": None, "max_dd_pct": None}
    ann = float(seg.mean()) * 252
    vol = float(seg.std(ddof=1)) * np.sqrt(252)
    cum = (1.0 + seg).cumprod()
    dd = float((cum / cum.cummax() - 1.0).min()) * 100
    return {"days": int(len(seg)),
            "sharpe": round(ann / vol, 2) if vol > 0 else 0.0,
            "max_dd_pct": round(dd, 2)}

def vol_spike_mask(index_ret: pd.Series, lookback: int = 252,
                   pctile: float = 90.0) -> pd.Series:
    rv = index_ret.rolling(20).std(ddof=1)
    ref = rv.rolling(lookback, min_periods=60).quantile(pctile / 100.0)
    return (rv > ref).fillna(False)

def stress_report(daily_net: pd.Series, index_ret: Optional[pd.Series] = None) -> dict:
    out = {}
    for name, (a, b) in WINDOWS.items():
        try:
            seg = daily_net.loc[a:b]
        except TypeError:
            seg = pd.Series(dtype=float)
        out[name] = _window_stats(seg)
    if index_ret is not None:
        mask = vol_spike_mask(index_ret).reindex(daily_net.index, fill_value=False)
        out["vol_spike"] = _window_stats(daily_net[mask])
    else:
        out["vol_spike"] = {"days": 0, "sharpe": None, "max_dd_pct": None}
    return out
===END===

===FILE: backend/tests/test_phase3_jev_research.py===
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
===END===
