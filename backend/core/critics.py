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
