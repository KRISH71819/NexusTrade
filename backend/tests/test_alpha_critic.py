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
