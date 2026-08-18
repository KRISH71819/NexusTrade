"""
Unit tests for the Phase-1 alpha sandbox: DSL safety + evaluator math.
No MongoDB / network needed.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from alpha_sandbox.dsl import validate_expression, evaluate_expression
from alpha_sandbox.evaluator import compute_metrics


def _df(n=120):
    prices = [100 + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1_000_000] * n,
    })


class TestDSLSafety:
    def test_rejects_import(self):
        ok, _ = validate_expression("__import__('os').system('ls')")
        assert not ok

    def test_rejects_attribute(self):
        ok, _ = validate_expression("close.mean")
        assert not ok

    def test_rejects_subscript(self):
        ok, _ = validate_expression("close[0]")
        assert not ok

    def test_rejects_unknown_name(self):
        ok, _ = validate_expression("close + secret")
        assert not ok

    def test_accepts_classic(self):
        ok, err = validate_expression("close / sma(close, 20) - 1")
        assert ok, err


class TestDSLEval:
    def test_momentum_sign_and_honesty(self):
        df = _df()
        s = evaluate_expression("close / sma(close, 20) - 1", df)
        assert s.iloc[-1] > 0                 # rising series → positive momentum
        assert s.iloc[:19].isna().all()       # full window required → no early fake values

    def test_logical_and_works(self):
        df = _df()
        s = evaluate_expression(
            "(close > sma(close, 20)) and (volume_ratio(volume, 20) > 0.5)", df
        )
        assert bool(s.iloc[-1])


class TestEvaluator:
    def test_zero_returns_zero_sharpe(self):
        m = compute_metrics(pd.Series([0.0] * 100))
        assert m["sharpe"] == 0.0

    def test_positive_series_positive_sharpe(self):
        rng = np.random.RandomState(42)
        m = compute_metrics(pd.Series(rng.normal(0.001, 0.01, 300)))
        assert m["sharpe"] > 0

    def test_max_dd_known(self):
        d = pd.Series([0.0] * 60 + [0.10, -0.05, 0.0])
        m = compute_metrics(d)
        assert -5.1 <= m["max_dd_pct"] <= -4.9
