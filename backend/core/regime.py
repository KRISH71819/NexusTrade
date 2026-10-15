"""Code-only regime ladder. Jev may only propose a bounded +/-1 step override."""
LADDER = ["CRISIS", "RISK_OFF", "NEUTRAL", "RISK_ON"]
CAPS = {"RISK_ON": 1.0, "NEUTRAL": 0.8, "RISK_OFF": 0.4, "CRISIS": 0.0}

def code_regime(breadth_pct: float, index_gap_pct: float, vol_percentile: float) -> dict:
    reasons = []
    if breadth_pct < 35:
        reasons.append(f"breadth {breadth_pct:.0f}% < 35%")
    if index_gap_pct < -6:
        reasons.append(f"index {index_gap_pct:.1f}% below 200d (>6%)")
    if vol_percentile > 90:
        reasons.append(f"vol pctile {vol_percentile:.0f} > 90")
    if reasons:
        state = "CRISIS"
    elif breadth_pct >= 60 and index_gap_pct >= 0:
        state = "RISK_ON"
    elif breadth_pct < 45 or index_gap_pct <= -3:
        state = "RISK_OFF"
    else:
        state = "NEUTRAL"
    return {"state": state, "cap": CAPS[state], "reasons": reasons}

def exposure_cap(state: str) -> float:
    return CAPS.get(state, 0.0)   # unknown state => fail-safe zero exposure

def apply_bounded_override(state: str, proposed: str) -> tuple:
    """Jev override: at most one ladder step. Returns (new_state, accepted)."""
    if proposed not in LADDER or state not in LADDER:
        return state, False
    if abs(LADDER.index(proposed) - LADDER.index(state)) <= 1:
        return proposed, True
    return state, False

PHASE4_CAPS = {
    "CRISIS": 0.0,
    "RISK_OFF": 0.50,
    "NEUTRAL": 0.80,
    "RISK_ON": 1.00,
}

def classify_regime(breadth_pct: float, index_gap_pct: float = 0.0, vol_percentile: float | None = None) -> dict:
    """Classifies market health to dictate target exposure according to Phase 4 Regime Matrix.

    Regime Matrix:
    - CRISIS:   Breadth < 20% OR Index Gap < -15%  -> Target Exposure 0%  (Liquidate all to cash)
    - RISK_OFF: Breadth < 40% OR Index Gap < -5%   -> Target Exposure 50% (Scale down positions)
    - NEUTRAL:  Breadth 40% - 70%                  -> Target Exposure 80% (Maintain standard sizing)
    - RISK_ON:  Breadth > 70% AND Index Gap > 0%   -> Target Exposure 100% (Fully invested)
    """
    if breadth_pct < 20.0 or index_gap_pct < -15.0:
        state = "CRISIS"
    elif breadth_pct < 40.0 or index_gap_pct < -5.0:
        state = "RISK_OFF"
    elif breadth_pct > 70.0 and index_gap_pct > 0.0:
        state = "RISK_ON"
    else:
        state = "NEUTRAL"

    exposure = PHASE4_CAPS[state]
    return {
        "regime": state,
        "state": state,
        "target_exposure": exposure,
        "exposure": exposure,
        "breadth_pct": float(breadth_pct),
        "index_gap_pct": float(index_gap_pct),
        "vol_percentile": float(vol_percentile) if vol_percentile is not None else None,
    }

