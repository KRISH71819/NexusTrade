"""
Tests for the multi-agent LLM chain (Groq analyst → Gemma reviewer).

Tests the chain orchestrator, verdict arbiter, and all fallback scenarios.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# ── Test fixtures ─────────────────────────────────────────────────────────────

MOCK_GROQ_RESULT = {
    "action": "BUY",
    "confidence": 0.78,
    "position_size_pct": 0.08,
    "risk_factors": ["high PE", "crude oil sensitivity"],
    "reasoning": "RSI oversold at 32, MACD bullish crossover",
    "news_impact_score": 0.45,
    "crisis_detected": False,
    "analyst_model": "groq",
    "groq_model_used": "groq/compound",
}

MOCK_GEMMA_RESULT = {
    "action": "BUY",
    "confidence": 0.72,
    "position_size_pct": 0.06,
    "risk_factors": ["sector rotation risk"],
    "reasoning": "Technical indicators bullish",
    "news_impact_score": 0.30,
    "crisis_detected": False,
    "analyst_model": "gemma",
}

MOCK_HOLD_RESULT = {
    "action": "HOLD",
    "confidence": 0.55,
    "position_size_pct": 0.0,
    "risk_factors": [],
    "reasoning": "No clear signal",
    "news_impact_score": 0.10,
    "crisis_detected": False,
    "analyst_model": "groq",
    "groq_model_used": "llama-3.3-70b-versatile",
}

MOCK_REVIEW_AGREE = {
    "verdict": "AGREE",
    "adjusted_confidence": 0.85,
    "missed_risks": [],
    "review_notes": "Analyst reasoning is sound",
}

MOCK_REVIEW_CAUTION = {
    "verdict": "CAUTION",
    "adjusted_confidence": 0.62,
    "missed_risks": ["crude oil above $90 compresses margins"],
    "review_notes": "Lower confidence due to commodity risk",
}

MOCK_REVIEW_VETO = {
    "verdict": "VETO",
    "adjusted_confidence": 0.20,
    "missed_risks": ["structural downtrend", "RSI oversold is a trap"],
    "review_notes": "Do not buy — falling channel",
}

ANALYSIS_KWARGS = {
    "ticker": "RELIANCE.NS",
    "technical_snapshot": {"rsi_14": 32, "macd_signal": 0.5},
    "macro_news": ["India GDP strong"],
    "sector_news": ["Energy sector bullish"],
    "stock_news": ["Q2 results beat"],
    "portfolio_state": {"cash": 50000, "total_value": 100000, "holdings": []},
    "risk_info": {"sector": "Energy", "sector_exposure_count": 1},
    "raw_context": {"price": 2450, "rsi": 32, "macd_signal": 0.5,
                    "volume_ratio": 1.2, "market_regime": "BULLISH", "cash_pct": 0.5},
}


# ── Verdict Arbiter Tests ─────────────────────────────────────────────────────

class TestApplyReviewVerdict:
    """Test _apply_review_verdict — the code-based arbiter."""

    def test_agree_boosts_confidence(self):
        from llm_engine import _apply_review_verdict
        result = _apply_review_verdict(MOCK_GROQ_RESULT.copy(), MOCK_REVIEW_AGREE)
        # AGREE should boost by 0.08 (chain_agree_boost)
        assert result["confidence"] == pytest.approx(0.78 + 0.08, abs=0.01)
        assert result["action"] == "BUY"
        assert result["review"]["verdict"] == "AGREE"

    def test_caution_lowers_confidence(self):
        from llm_engine import _apply_review_verdict
        result = _apply_review_verdict(MOCK_GROQ_RESULT.copy(), MOCK_REVIEW_CAUTION)
        # CAUTION should use reviewer's adjusted_confidence
        assert result["confidence"] == pytest.approx(0.62, abs=0.01)
        assert result["action"] == "BUY"
        assert result["review"]["verdict"] == "CAUTION"
        assert len(result["review"]["missed_risks"]) > 0

    def test_caution_respects_floor(self):
        from llm_engine import _apply_review_verdict
        low_caution = {
            **MOCK_REVIEW_CAUTION,
            "adjusted_confidence": 0.20,  # below floor (0.50)
        }
        result = _apply_review_verdict(MOCK_GROQ_RESULT.copy(), low_caution)
        assert result["confidence"] >= 0.50  # must not go below floor

    def test_veto_forces_hold(self):
        from llm_engine import _apply_review_verdict
        result = _apply_review_verdict(MOCK_GROQ_RESULT.copy(), MOCK_REVIEW_VETO)
        assert result["action"] == "HOLD"
        assert result["confidence"] == 0.30
        assert result["review"]["verdict"] == "VETO"

    def test_agree_caps_at_one(self):
        from llm_engine import _apply_review_verdict
        high_conf_result = {**MOCK_GROQ_RESULT, "confidence": 0.98}
        result = _apply_review_verdict(high_conf_result, MOCK_REVIEW_AGREE)
        assert result["confidence"] <= 1.0


# ── Chain Orchestrator Tests ──────────────────────────────────────────────────

class TestAnalyzeWithLLM:
    """Test analyze_with_llm chain orchestrator."""

    @pytest.mark.asyncio
    async def test_groq_analyst_gemma_reviewer_agree(self):
        """Normal chain: Groq BUY → Gemma AGREE → confidence boosted."""
        with patch("llm_engine.settings") as mock_settings, \
             patch("llm_engine.analyze_with_gemma", new_callable=AsyncMock) as mock_gemma, \
             patch("llm_engine.review_with_gemma", new_callable=AsyncMock) as mock_review:

            mock_settings.llm_mode = "chain"
            mock_settings.groq_api_key = "test-key"
            mock_settings.groq_timeout = 30.0
            mock_settings.chain_agree_boost = 0.08
            mock_settings.chain_caution_floor = 0.50
            mock_settings.gemini_model = "gemma-4-31b-it"

            mock_review.return_value = MOCK_REVIEW_AGREE

            with patch("groq_engine.analyze_with_groq", new_callable=AsyncMock) as mock_groq:
                mock_groq.return_value = MOCK_GROQ_RESULT.copy()

                from llm_engine import analyze_with_llm
                result = await analyze_with_llm(**ANALYSIS_KWARGS)

                assert result["action"] == "BUY"
                assert result["confidence"] > 0.78  # boosted
                assert result["review"]["verdict"] == "AGREE"
                mock_groq.assert_called_once()
                mock_review.assert_called_once()
                mock_gemma.assert_not_called()  # shouldn't fall back

    @pytest.mark.asyncio
    async def test_groq_down_gemma_auto_promotes(self):
        """Groq timeout → Gemma becomes analyst, no reviewer called."""
        with patch("llm_engine.settings") as mock_settings, \
             patch("llm_engine.analyze_with_gemma", new_callable=AsyncMock) as mock_gemma, \
             patch("llm_engine.review_with_gemma", new_callable=AsyncMock) as mock_review:

            mock_settings.llm_mode = "chain"
            mock_settings.groq_api_key = "test-key"
            mock_settings.groq_timeout = 0.001  # instant timeout

            mock_gemma.return_value = MOCK_GEMMA_RESULT.copy()

            with patch("groq_engine.analyze_with_groq", new_callable=AsyncMock) as mock_groq:
                # Simulate Groq taking too long
                async def slow_groq(*args, **kwargs):
                    await asyncio.sleep(10)
                    return MOCK_GROQ_RESULT
                mock_groq.side_effect = slow_groq

                from llm_engine import analyze_with_llm
                result = await analyze_with_llm(**ANALYSIS_KWARGS)

                assert result["analyst_model"] == "gemma"
                assert result["review"] is None  # no self-review
                mock_gemma.assert_called_once()
                mock_review.assert_not_called()

    @pytest.mark.asyncio
    async def test_hold_skips_reviewer(self):
        """Groq says HOLD → reviewer NOT called."""
        with patch("llm_engine.settings") as mock_settings, \
             patch("llm_engine.review_with_gemma", new_callable=AsyncMock) as mock_review:

            mock_settings.llm_mode = "chain"
            mock_settings.groq_api_key = "test-key"
            mock_settings.groq_timeout = 30.0

            with patch("groq_engine.analyze_with_groq", new_callable=AsyncMock) as mock_groq:
                mock_groq.return_value = MOCK_HOLD_RESULT.copy()

                from llm_engine import analyze_with_llm
                result = await analyze_with_llm(**ANALYSIS_KWARGS)

                assert result["action"] == "HOLD"
                assert result["review"]["skipped"] is True
                mock_review.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_groq_key_degrades(self):
        """No groq_api_key → auto-degrades to Gemma-only (single mode)."""
        with patch("llm_engine.settings") as mock_settings, \
             patch("llm_engine.analyze_with_gemma", new_callable=AsyncMock) as mock_gemma, \
             patch("llm_engine.review_with_gemma", new_callable=AsyncMock) as mock_review:

            mock_settings.llm_mode = "chain"
            mock_settings.groq_api_key = ""  # no key
            mock_settings.groq_timeout = 30.0

            mock_gemma.return_value = MOCK_GEMMA_RESULT.copy()

            from llm_engine import analyze_with_llm
            result = await analyze_with_llm(**ANALYSIS_KWARGS)

            assert result["analyst_model"] == "gemma"
            mock_review.assert_not_called()

    @pytest.mark.asyncio
    async def test_groq_analyst_gemma_reviewer_veto(self):
        """Groq BUY → Gemma VETO → action forced to HOLD."""
        with patch("llm_engine.settings") as mock_settings, \
             patch("llm_engine.review_with_gemma", new_callable=AsyncMock) as mock_review:

            mock_settings.llm_mode = "chain"
            mock_settings.groq_api_key = "test-key"
            mock_settings.groq_timeout = 30.0
            mock_settings.chain_agree_boost = 0.08
            mock_settings.chain_caution_floor = 0.50
            mock_settings.gemini_model = "gemma-4-31b-it"

            mock_review.return_value = MOCK_REVIEW_VETO

            with patch("groq_engine.analyze_with_groq", new_callable=AsyncMock) as mock_groq:
                mock_groq.return_value = MOCK_GROQ_RESULT.copy()

                from llm_engine import analyze_with_llm
                result = await analyze_with_llm(**ANALYSIS_KWARGS)

                assert result["action"] == "HOLD"
                assert result["confidence"] == 0.30
                assert result["review"]["verdict"] == "VETO"

    @pytest.mark.asyncio
    async def test_groq_error_fallback(self):
        """Groq throws exception → Gemma takes over as analyst."""
        with patch("llm_engine.settings") as mock_settings, \
             patch("llm_engine.analyze_with_gemma", new_callable=AsyncMock) as mock_gemma:

            mock_settings.llm_mode = "chain"
            mock_settings.groq_api_key = "test-key"
            mock_settings.groq_timeout = 30.0

            mock_gemma.return_value = MOCK_GEMMA_RESULT.copy()

            with patch("groq_engine.analyze_with_groq", new_callable=AsyncMock) as mock_groq:
                mock_groq.side_effect = RuntimeError("Connection refused")

                from llm_engine import analyze_with_llm
                result = await analyze_with_llm(**ANALYSIS_KWARGS)

                assert result["analyst_model"] == "gemma"
                mock_gemma.assert_called_once()

    @pytest.mark.asyncio
    async def test_reviewer_error_uses_analyst_only(self):
        """Reviewer throws → use Groq result as-is (no crash)."""
        with patch("llm_engine.settings") as mock_settings, \
             patch("llm_engine.review_with_gemma", new_callable=AsyncMock) as mock_review:

            mock_settings.llm_mode = "chain"
            mock_settings.groq_api_key = "test-key"
            mock_settings.groq_timeout = 30.0
            mock_settings.gemini_model = "gemma-4-31b-it"

            mock_review.side_effect = RuntimeError("Gemma API error")

            with patch("groq_engine.analyze_with_groq", new_callable=AsyncMock) as mock_groq:
                mock_groq.return_value = MOCK_GROQ_RESULT.copy()

                from llm_engine import analyze_with_llm
                result = await analyze_with_llm(**ANALYSIS_KWARGS)

                assert result["action"] == "BUY"
                assert result["confidence"] == 0.78  # unchanged
                assert result["review"]["skipped"] is True

    @pytest.mark.asyncio
    async def test_reviewer_returns_none_uses_analyst_only(self):
        """Reviewer returns None (parse error) → use Groq result as-is."""
        with patch("llm_engine.settings") as mock_settings, \
             patch("llm_engine.review_with_gemma", new_callable=AsyncMock) as mock_review:

            mock_settings.llm_mode = "chain"
            mock_settings.groq_api_key = "test-key"
            mock_settings.groq_timeout = 30.0
            mock_settings.gemini_model = "gemma-4-31b-it"

            mock_review.return_value = None  # parse failed

            with patch("groq_engine.analyze_with_groq", new_callable=AsyncMock) as mock_groq:
                mock_groq.return_value = MOCK_GROQ_RESULT.copy()

                from llm_engine import analyze_with_llm
                result = await analyze_with_llm(**ANALYSIS_KWARGS)

                assert result["action"] == "BUY"
                assert result["confidence"] == 0.78  # unchanged
                assert result["review"]["skipped"] is True
