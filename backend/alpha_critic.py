"""
Alpha Critic (Phase 3) — adversarial LLM pre-screen for candidate alphas.
Sits between the generator and the sandbox: a candidate must survive the
critic BEFORE we spend backtest compute on it.

Checks (semantic, not mechanical — the DSL is causal by construction):
 1. Overfitting: too many multiplied conditions / hand-tuned thresholds
 2. Artifact loading: volume/volatility-spike selection without a trend anchor
    (pre-2010 NSE data has corporate-action artifacts on exactly those days)
 3. Churn vs ~1.1% round-trip cost
 4. Degenerate exposure (~0% or ~100% long)
 5. Hypothesis contradicts the expression / no economic rationale

Verdicts: APPROVE / REVISE / REJECT. Only APPROVE reaches the sandbox.
Unparseable output or missing LLM => REJECT (fail-closed).
Offline only — never import from main.py / scheduler.py.
"""
import json
import logging
import re
import time

from alpha_generator import _call_groq, _call_gemma

logger = logging.getLogger(__name__)

_CRITIC_PROMPT = """You are a hostile quantitative research reviewer. Your job is to KILL bad trading rules before they waste backtest compute. Be concise and specific.

Candidate rule for NSE Indian equities (daily bars).
SCORE semantics: score > 0 => LONG next day, else CASH.
The expression language is causal by construction (rolling/shift only), so do NOT flag mechanical look-ahead. Flag SEMANTIC problems only.

EXPRESSION: {expr}
HYPOTHESIS: {hypothesis}

Checklist (flag only real violations):
1. OVERFITTING: more than 3 multiplied boolean conditions, or more than 2 hand-tuned non-standard thresholds.
2. ARTIFACT LOADING: selection driven by raw volume spikes or volatility spikes WITHOUT a price/trend anchor.
3. CHURN: entry condition flips daily (e.g. delta(close,1) as the only anchor) vs ~1.1% round-trip cost.
4. DEGENERATE EXPOSURE: likely long ~100% of days (no selectivity) or ~0% (untestable).
5. ECONOMIC NONSENSE: hypothesis contradicts the expression, or no plausible rationale.

Return ONLY JSON: {{"verdict": "APPROVE" or "REVISE" or "REJECT", "reasons": ["..."], "fatal_flaw": ""}}
APPROVE only if no fatal flaw. REVISE if fixable with a small change. REJECT if structurally wrong."""


def _parse_critique(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        return {"verdict": "REJECT",
                "reasons": ["critic output unparseable (fail-closed)"],
                "fatal_flaw": ""}
    verdict = str(data.get("verdict", "REJECT")).upper()
    if verdict not in ("APPROVE", "REVISE", "REJECT"):
        verdict = "REJECT"
    reasons = data.get("reasons", [])
    if isinstance(reasons, str):
        reasons = [reasons]
    reasons = [str(r) for r in reasons][:6]
    return {"verdict": verdict,
            "reasons": reasons,
            "fatal_flaw": str(data.get("fatal_flaw", ""))[:300]}


def check_structural_rejections(expr: str) -> dict | None:
    """
    Pre-sandbox structural rejections (Section 4 A & B) — fires BEFORE LLM or sandbox:
    1. Multi-conjunction: 3+ AND-conjoined threshold conditions -> REJECT ('selectivity risk')
    2. Fast-cross: lookback < 60 days in entry / fast oscillator -> REJECT ('structural turnover too high')
    """
    if not expr or not isinstance(expr, str):
        return None

    import ast

    # ── A. Multi-conjunction detector (3+ AND-conjoined conditions) ──────────
    try:
        tree = ast.parse(expr, mode="eval")

        def count_and_nodes(node):
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
                return sum(count_and_nodes(v) for v in node.values)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitAnd, ast.And)):
                return count_and_nodes(node.left) + count_and_nodes(node.right)
            return 1

        if count_and_nodes(tree.body) >= 3:
            return {
                "verdict": "REJECT",
                "reasons": ["selectivity risk: 3+ conjunctions never fire"],
                "fatal_flaw": "selectivity risk: 3+ conjunctions never fire",
            }
    except Exception:
        pass

    and_tokens = re.split(r'\s+and\s+|\s*&\s*', expr, flags=re.IGNORECASE)
    if len(and_tokens) >= 3:
        return {
            "verdict": "REJECT",
            "reasons": ["selectivity risk: 3+ conjunctions never fire"],
            "fatal_flaw": "selectivity risk: 3+ conjunctions never fire",
        }

    # ── B. Fast-cross detector (lookback < 60 days / fast oscillator) ─────────
    fast_funcs = {"macd", "macd_hist", "macd_cross"}
    try:
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name.lower() in fast_funcs:
                    return {
                        "verdict": "REJECT",
                        "reasons": ["structural turnover too high — fast oscillator"],
                        "fatal_flaw": "structural turnover too high — fast oscillator",
                    }

                # Check integer lookback arguments (< 60)
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                        if 0 < arg.value < 60:
                            return {
                                "verdict": "REJECT",
                                "reasons": ["structural turnover too high — fast oscillator"],
                                "fatal_flaw": "structural turnover too high — fast oscillator",
                            }
    except Exception:
        pass

    if re.search(r'\b(macd|macd_hist|macd_cross)\b', expr, flags=re.IGNORECASE):
        return {
            "verdict": "REJECT",
            "reasons": ["structural turnover too high — fast oscillator"],
            "fatal_flaw": "structural turnover too high — fast oscillator",
        }

    func_calls = re.findall(
        r'\b(sma|ema|std|delta|zscore|rank|rsi|volume_ratio|macd_cross)\s*\([^)]*\)',
        expr, flags=re.IGNORECASE
    )
    for call_str in func_calls:
        nums = [int(n) for n in re.findall(r'\b\d+\b', call_str)]
        if any(0 < n < 60 for n in nums):
            return {
                "verdict": "REJECT",
                "reasons": ["structural turnover too high — fast oscillator"],
                "fatal_flaw": "structural turnover too high — fast oscillator",
            }

    return None


def critique_candidate(candidate: dict) -> dict:
    """Run the critic over one candidate. Applies structural checks first, then LLM. Fail-closed on any failure."""
    expr = candidate.get("expression", "")
    structural = check_structural_rejections(expr)
    if structural:
        logger.info(f"CRITIC {structural['verdict']} (structural pre-check) for {candidate.get('name', '?')}: "
                    f"{structural['fatal_flaw']}")
        return structural

    prompt = _CRITIC_PROMPT.format(
        expr=expr,
        hypothesis=candidate.get("hypothesis", ""),
    )
    time.sleep(2.1)  # rate-limit spacing after the generator call
    raw = _call_groq(prompt) or _call_gemma(prompt)
    if not raw:
        return {"verdict": "REJECT",
                "reasons": ["critic LLM unavailable (fail-closed)"],
                "fatal_flaw": ""}
    critique = _parse_critique(raw)
    logger.info(f"CRITIC {critique['verdict']} for {candidate.get('name', '?')}: "
                f"{critique['fatal_flaw'] or '; '.join(critique['reasons'])}")
    return critique
