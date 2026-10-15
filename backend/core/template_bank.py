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
