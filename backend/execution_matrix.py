"""
Execution Matrix — weighted scoring system replacing rigid threshold gates.

Final Score = (
    0.40 × gemini_confidence +    # Gemini is primary (includes news context)
    0.25 × ml_confidence +         # ML technical confirmation
    0.20 × news_impact_score +     # Direct news impact
    0.15 × risk_adjustment         # Risk manager input
)

Decision Rules:
    BUY:  final_score > 0.60 AND crisis_detected == False AND risk_approved
          AND market_regime == BULLISH AND volume_ratio >= 1.2
    SELL: final_score < 0.30 OR crisis_detected OR stop_loss_hit
    HOLD: everything else
"""

import logging
from models import TradeAction, RiskAssessment
from config import settings

logger = logging.getLogger(__name__)


def compute_final_score(
    gemini_confidence: float,
    ml_confidence: float,
    news_impact_score: float,
    risk_adjustment: float,
) -> float:
    """
    Compute the weighted final score from all signal sources.

    Args:
        gemini_confidence: 0.0-1.0, from Gemini structured analysis
        ml_confidence: 0.0-1.0, from ML ensemble
        news_impact_score: -1.0 to 1.0, from news intelligence
        risk_adjustment: -1.0 to 1.0, from risk manager

    Returns:
        float: 0.0-1.0 final composite score
    """
    # Normalize news_impact_score from [-1, 1] to [0, 1]
    news_normalized = (news_impact_score + 1.0) / 2.0

    # Normalize risk_adjustment from [-1, 1] to [0, 1]
    risk_normalized = (risk_adjustment + 1.0) / 2.0

    score = (
        settings.weight_gemini * gemini_confidence +
        settings.weight_ml * ml_confidence +
        settings.weight_news * news_normalized +
        settings.weight_risk * risk_normalized
    )

    return max(0.0, min(1.0, score))


def decide_action(
    final_score: float,
    gemini_action: str,
    crisis_detected: bool,
    risk_assessment: RiskAssessment,
    has_position: bool,
    market_regime: str = "BULLISH",
    volume_ratio: float = 1.0,
) -> TradeAction:
    """
    Decision matrix using weighted score + override conditions.

    Priority order:
    1. Crisis -> SELL (if holding) or HOLD (if not)
    2. Stop-loss/trailing stop hit -> SELL
    3. Market regime gate (bearish → block new BUYs)
    4. Volume confirmation gate (weak volume → block new BUYs)
    5. Score-based decision with risk gates
    """
    # ── Override 1: Crisis (SMART — severity-based) ────────────────────────
    # Only hard-block on SEVERE crises (severity >= 0.7)
    # Moderate crises just penalize the score (handled by news_impact in score)
    if crisis_detected:
        # Check if risk_assessment has crisis severity info
        crisis_flags = [f for f in risk_assessment.risk_flags if "CRISIS" in f.upper()]
        is_severe = len(crisis_flags) >= 2  # multiple crisis flags = severe

        if is_severe:
            if has_position:
                logger.info("SEVERE CRISIS -> SELL (protecting position)")
                return TradeAction.SELL
            else:
                logger.info("SEVERE CRISIS -> HOLD (not buying into severe crisis)")
                return TradeAction.HOLD
        else:
            # Moderate crisis: let the score decide, but log the warning
            logger.info(
                f"Moderate crisis detected - proceeding with score-based decision "
                f"(score={final_score:.2f})"
            )

    # ── Override 2: Stop-loss / trailing stop ────────────────────────────
    for flag in risk_assessment.risk_flags:
        if "STOP-LOSS HIT" in flag or "TRAILING STOP HIT" in flag:
            logger.info(f"RISK OVERRIDE -> SELL ({flag})")
            return TradeAction.SELL

    # ── Override 3: Drawdown halt (no new buys) ──────────────────────────
    drawdown_halt = risk_assessment.portfolio_drawdown_pct > settings.max_drawdown_pct

    # ── Override 4: MARKET REGIME GATE (bearish → block all new BUYs) ────
    # Professional funds NEVER initiate new long positions in a confirmed
    # downtrend. Existing positions are managed by trailing stops.
    regime_blocked = False
    if market_regime == "BEARISH" and not has_position:
        regime_blocked = True
        logger.info(
            f"REGIME GATE: Market is BEARISH (NIFTY < SMA50) → blocking new BUY. "
            f"Score={final_score:.2f} would have qualified otherwise."
        )

    # ── Override 5: VOLUME CONFIRMATION GATE ─────────────────────────────
    # Don't buy breakouts on weak volume — they're statistically more likely
    # to be false signals. Require volume ≥ 1.2× 20-day average.
    volume_blocked = False
    if volume_ratio < settings.min_volume_ratio and not has_position:
        volume_blocked = True
        logger.info(
            f"VOLUME GATE: Volume ratio {volume_ratio:.2f}x < "
            f"{settings.min_volume_ratio}x required → blocking BUY"
        )

    # ── Score-based decision ─────────────────────────────────────────────
    if (final_score >= 0.60
            and not drawdown_halt
            and not regime_blocked
            and not volume_blocked
            and risk_assessment.risk_approved):
        # Additional check: Gemini must also agree (or at least not disagree)
        if gemini_action in ("BUY", "HOLD"):
            return TradeAction.BUY
        else:
            # Score says BUY but Gemini says SELL — hold and wait
            logger.info(
                f"Score {final_score:.2f} suggests BUY but Gemini says {gemini_action} -> HOLD"
            )
            return TradeAction.HOLD

    elif final_score < 0.30:
        if has_position:
            return TradeAction.SELL
        else:
            return TradeAction.HOLD

    else:
        # Middle ground — check if Gemini has a strong opinion
        if gemini_action == "SELL" and has_position and final_score < 0.45:
            return TradeAction.SELL
        return TradeAction.HOLD


def build_action_reason(
    action: TradeAction,
    final_score: float,
    gemini_confidence: float,
    ml_confidence: float,
    news_impact_score: float,
    risk_adjustment: float,
    gemini_reasoning: str = "",
    risk_flags: list = None,
    crisis_detected: bool = False,
    market_regime: str = "BULLISH",
    volume_ratio: float = 1.0,
) -> str:
    """Build a human-readable, fully transparent reason for the trade decision."""

    score_breakdown = (
        f"Final Score: {final_score:.2f} "
        f"[Gemini {gemini_confidence:.0%} × {settings.weight_gemini:.0%} + "
        f"ML {ml_confidence:.0%} × {settings.weight_ml:.0%} + "
        f"News {news_impact_score:+.2f} × {settings.weight_news:.0%} + "
        f"Risk {risk_adjustment:+.2f} × {settings.weight_risk:.0%}]"
    )

    # Add regime and volume context
    regime_info = f" Regime: {market_regime}."
    volume_info = f" Vol: {volume_ratio:.1f}x avg." if volume_ratio != 1.0 else ""

    if crisis_detected:
        reason = f"CRISIS OVERRIDE: {gemini_reasoning[:200]}. {score_breakdown}{regime_info}"
    elif action == TradeAction.BUY:
        reason = (
            f"BUY signal: {score_breakdown}. "
            f"Gemini: {gemini_reasoning[:200]}{regime_info}{volume_info}"
        )
    elif action == TradeAction.SELL:
        risk_info = ""
        if risk_flags:
            risk_info = f" Risk: {'; '.join(risk_flags[:3])}."
        reason = (
            f"SELL signal: {score_breakdown}. "
            f"Gemini: {gemini_reasoning[:200]}.{risk_info}{regime_info}"
        )
    else:
        blocked_reason = ""
        if market_regime == "BEARISH":
            blocked_reason = " BEARISH REGIME: new buys blocked."
        if volume_ratio < settings.min_volume_ratio:
            blocked_reason += f" LOW VOLUME ({volume_ratio:.1f}x): buy blocked."
        reason = (
            f"HOLD: {score_breakdown}. "
            f"Neither BUY (≥0.60) nor SELL (<0.30) threshold met. "
            f"Gemini: {gemini_reasoning[:150]}{blocked_reason}{regime_info}"
        )

    return reason
