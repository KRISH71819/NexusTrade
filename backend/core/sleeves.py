"""Sleeve signal definitions + pure weight helpers. Max 4 sleeves."""
import pandas as pd
from core.dsl import evaluate_expression

SLEEVE_SIGNALS = {
    "M": "close / sma(close, 200) - 1",          # trend_200 (validated, capital day-1)
    "D": "-(std(close, 120) / close)",           # low-vol defensive
    "R": "-(close / sma(close, 21) - 1)",        # rev_21 mean-reversion satellite
    "V": "(close / sma(close, 60) - 1) * volume_ratio(volume, 20)",  # volmom_60
}
MAX_SLEEVES = 4
GRADUATED_DAY1 = ["M"]

def sleeve_score(panel: dict, sleeve: str) -> dict:
    """Last-bar cross-sectional score per ticker. Higher = better long."""
    expr = SLEEVE_SIGNALS[sleeve]
    out = {}
    for ticker, df in panel.items():
        try:
            indexed = df.set_index("date").sort_index()
            s = evaluate_expression(expr, indexed).dropna()
            if len(s):
                out[ticker] = float(s.iloc[-1])
        except Exception:
            continue
    return out

def equal_weights(names: list) -> dict:
    if not names:
        return {}
    return {n: 1.0 / len(names) for n in names}

def inverse_vol_weights(vols: dict) -> dict:
    """Monthly reweight: trailing inverse-vol among graduated sleeves."""
    pos = {k: (1.0 / v) for k, v in vols.items() if v and v > 0}
    if not pos:
        return equal_weights(list(vols))
    total = sum(pos.values())
    return {k: v / total for k, v in pos.items()}
