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
