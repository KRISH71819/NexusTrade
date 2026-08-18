"""Pure-function tests for the Phase-7 meta-scorer. No DB needed."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pandas as pd

from alpha_sandbox.meta_scorer import (
    cross_sectional_ic, blend_weights, market_regime_series,
)


def test_ic_signs():
    s = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0})
    assert cross_sectional_ic(s, s * 2) > 0.99
    assert cross_sectional_ic(s, -s) < -0.99
    assert np.isnan(cross_sectional_ic(s.iloc[:2], s.iloc[:2]))


def test_blend_weights_positive_wins():
    hist = {"good": [0.2] * 15, "bad": [-0.1] * 15}
    w, raw = blend_weights(hist)
    assert w["good"] == 1.0 and w["bad"] == 0.0
    assert raw["bad"] < 0


def test_blend_weights_equal_when_cold_start():
    w, _ = blend_weights({"a": [], "b": []})
    assert abs(w["a"] - 0.5) < 1e-9


def test_regime_false_after_crash():
    up = list(np.linspace(100, 150, 300))
    down = list(np.linspace(150, 90, 120))
    close = pd.DataFrame({"A": up + down, "B": up + down})
    reg = market_regime_series(close, sma_window=200)
    assert bool(reg.iloc[-1]) is False
    assert bool(reg.iloc[250]) is True
