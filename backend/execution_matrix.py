"""
Execution Matrix — combines ML probability + LLM sentiment to make trade decisions.
Phase 2: Will be fully implemented after user confirmation.
"""

import logging
from models import TradeAction, AnalysisResult
from config import settings

logger = logging.getLogger(__name__)


def decide_action(ml_confidence: float, sentiment_score: float) -> TradeAction:
    """
    Decision matrix:
        ML > 0.65 AND Sentiment > 0.3  → BUY
        ML < 0.35 AND Sentiment < -0.3 → SELL
        Otherwise                       → HOLD
    """
    if (
        ml_confidence > settings.ml_buy_threshold
        and sentiment_score > settings.llm_buy_threshold
    ):
        return TradeAction.BUY
    elif (
        ml_confidence < settings.ml_sell_threshold
        and sentiment_score < settings.llm_sell_threshold
    ):
        return TradeAction.SELL
    else:
        return TradeAction.HOLD


def build_action_reason(
    action: TradeAction,
    ml_confidence: float,
    sentiment_score: float,
) -> str:
    """Build a human-readable reason for the trade decision."""
    if action == TradeAction.BUY:
        return (
            f"BUY signal: ML confidence {ml_confidence:.0%} exceeds {settings.ml_buy_threshold:.0%} threshold, "
            f"sentiment {sentiment_score:+.2f} exceeds {settings.llm_buy_threshold:+.2f} threshold."
        )
    elif action == TradeAction.SELL:
        return (
            f"SELL signal: ML confidence {ml_confidence:.0%} below {settings.ml_sell_threshold:.0%} threshold, "
            f"sentiment {sentiment_score:+.2f} below {settings.llm_sell_threshold:+.2f} threshold."
        )
    else:
        return (
            f"HOLD: ML confidence {ml_confidence:.0%}, sentiment {sentiment_score:+.2f} — "
            f"neither BUY nor SELL thresholds met."
        )
