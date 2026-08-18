"""Phase 5 — cadence / min-hold state machine tests. No DB needed."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd
from alpha_sandbox.sandbox import _apply_position_rules

def test_cadence_blocks_off_decision_entries():
    want = pd.Series([1,0,0,0,0, 0,1,0,0,0, 1,0,0])
    out = _apply_position_rules(want, cadence_days=5, min_hold_days=0)
    assert out.tolist() == [1,1,1,1,1, 0,0,0,0,0, 1,1,1]

def test_min_hold_delays_exit():
    want = pd.Series([1,0,0,0,0,0,0,0,0,0])
    out = _apply_position_rules(want, cadence_days=1, min_hold_days=5)
    assert out.tolist() == [1,1,1,1,1, 0,0,0,0,0]

def test_disabled_rules_passthrough():
    want = pd.Series([1,0,1,0])
    out = _apply_position_rules(want, cadence_days=1, min_hold_days=0)
    assert out.tolist() == [1,0,1,0]
