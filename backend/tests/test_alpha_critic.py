"""Parser + fail-closed tests for the Phase-3 critic. No LLM calls."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alpha_critic import _parse_critique


class TestParseCritique:
    def test_approve_parsed(self):
        r = _parse_critique('```json\n{"verdict": "APPROVE", "reasons": ["ok"], "fatal_flaw": ""}\n```')
        assert r["verdict"] == "APPROVE" and r["reasons"] == ["ok"]

    def test_lowercase_verdict_normalized(self):
        r = _parse_critique('{"verdict": "revise", "reasons": "add trend anchor"}')
        assert r["verdict"] == "REVISE" and isinstance(r["reasons"], list)

    def test_garbage_fails_closed(self):
        r = _parse_critique("I think this rule is fine, let it pass.")
        assert r["verdict"] == "REJECT"

    def test_unknown_verdict_fails_closed(self):
        r = _parse_critique('{"verdict": "MAYBE", "reasons": []}')
        assert r["verdict"] == "REJECT"


class TestStructuralRejections:
    """Pre-sandbox structural rejections (Section 4 A & B) — fires BEFORE LLM or sandbox."""

    def test_multi_conjunction_rejected_with_selectivity_risk(self):
        """Spec 5.2(c): 3+ AND conditions rejected with selectivity risk."""
        from alpha_critic import check_structural_rejections, critique_candidate

        expr = "rsi(close, 14) < 30 and zscore(close, 10) < -1.5 and volume_ratio(volume, 10) > 1.5"
        res = check_structural_rejections(expr)
        assert res is not None
        assert res["verdict"] == "REJECT"
        assert "selectivity risk" in res["fatal_flaw"]

        # Also via critique_candidate directly without LLM
        cand = {"name": "test_3_conj", "expression": expr, "hypothesis": "overfitting"}
        critique = critique_candidate(cand)
        assert critique["verdict"] == "REJECT"
        assert "selectivity risk" in critique["fatal_flaw"]

    def test_fast_cross_rejected_with_structural_turnover(self):
        """Spec 5.2(d): fast oscillator (lookback < 60) rejected with structural turnover too high."""
        from alpha_critic import check_structural_rejections, critique_candidate

        expr = "macd_cross(12,26,9) > 0"
        res = check_structural_rejections(expr)
        assert res is not None
        assert res["verdict"] == "REJECT"
        assert "structural turnover too high" in res["fatal_flaw"]

        cand = {"name": "test_macd", "expression": expr, "hypothesis": "fast cross"}
        critique = critique_candidate(cand)
        assert critique["verdict"] == "REJECT"
        assert "structural turnover too high" in critique["fatal_flaw"]

    def test_slow_ranking_signal_passes_structural_checks(self):
        """Valid slow signal with lookback >= 60 passes structural checks."""
        from alpha_critic import check_structural_rejections

        expr = "rank(close / sma(close, 200) - 1, 200)"
        res = check_structural_rejections(expr)
        assert res is None
