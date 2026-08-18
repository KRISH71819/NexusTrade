"""Pure promotion-predicate tests for Phase 4. No DB needed."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alpha_sandbox.hall_of_fame import is_promotable


def _doc(sharpe_gate, stability, alpha_dd, bench_dd=None):
    return {
        "gates": {"sharpe": sharpe_gate, "stability": stability},
        "metrics": {"max_dd_pct": alpha_dd},
        "info": {"bench_max_dd_pct": bench_dd},
    }


class TestPromotion:
    def test_requires_sharpe_and_stability(self):
        assert not is_promotable(_doc(False, True, -10, -40), dd_mode="relative")
        assert not is_promotable(_doc(True, False, -10, -40), dd_mode="relative")

    def test_relative_dd_boundary(self):
        # bench -40, rel 0.75 -> threshold -30
        assert is_promotable(_doc(True, True, -30, -40), dd_mode="relative", dd_relative=0.75)
        assert not is_promotable(_doc(True, True, -31, -40), dd_mode="relative", dd_relative=0.75)

    def test_relative_requires_benchmark(self):
        assert not is_promotable(_doc(True, True, -10, None), dd_mode="relative")

    def test_absolute_mode(self):
        assert is_promotable(_doc(True, True, -20), dd_mode="absolute", abs_dd_pct=25)
        assert not is_promotable(_doc(True, True, -26), dd_mode="absolute", abs_dd_pct=25)
