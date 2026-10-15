import numpy as np
import pandas as pd
from core.gates import compute_metrics, walk_forward_sharpes, apply_gates
from core.universe import is_qualified, panel_size_ok

def test_metrics_basic():
    assert compute_metrics(pd.Series([0.0] * 100))["sharpe"] == 0.0
    rng = np.random.RandomState(42)
    assert compute_metrics(pd.Series(rng.normal(0.001, 0.01, 300)))["sharpe"] > 0
    m = compute_metrics(pd.Series([0.0] * 60 + [0.10, -0.05, 0.0]))
    assert -5.1 <= m["max_dd_pct"] <= -4.9

def test_gates_all_pass():
    g = apply_gates({"sharpe": 1.2, "max_dd_pct": -20, "ann_turnover": 10},
                    [1.0, 0.8, 1.1], bench_max_dd_pct=-40)
    assert g["all"] and g["sharpe"] and g["max_dd"] and g["stability"] and g["turnover"]

def test_each_gate_can_kill_all():
    base = {"sharpe": 1.2, "max_dd_pct": -20, "ann_turnover": 10}
    folds = [1.0, 0.8, 1.1]
    assert apply_gates({**base, "sharpe": 0.9}, folds, -40)["all"] is False
    assert apply_gates({**base, "max_dd_pct": -31}, folds, -40)["all"] is False
    assert apply_gates({**base, "ann_turnover": 25}, folds, -40)["all"] is False
    assert apply_gates(base, [-0.5, -0.2, 0.4], -40)["all"] is False
    assert apply_gates(base, folds, -40, benchmark_clone=True)["all"] is False
    assert apply_gates(base, folds, -40, universe_ok=False)["all"] is False
    assert apply_gates(base, folds, None)["all"] is False   # missing benchmark => dd gate fails

def test_universe_rules():
    assert is_qualified(1500, 5.0e7, 50.0)
    assert not is_qualified(1499, 5.0e7, 50.0)
    assert not is_qualified(1500, 4.9e7, 50.0)
    assert not is_qualified(1500, 5.0e7, 49.0)
    assert panel_size_ok(100, 25)
    assert not panel_size_ok(99, 25)
