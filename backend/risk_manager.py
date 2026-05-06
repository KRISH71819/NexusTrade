"""
Risk Manager — position-level and portfolio-level risk controls.

Features:
  1. Stop-loss: auto-SELL if position drops X% from entry
  2. Trailing stop: after Y% gain, set a trailing stop at Z% from peak
  3. Sector concentration: max N stocks from same sector
  4. Max drawdown: halt buying if portfolio drops X% from peak
  5. Position sizing: confidence-based sizing
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

from config import settings
from models import RiskAssessment
from news_intelligence import get_sector

logger = logging.getLogger(__name__)


def assess_risk(
    ticker: str,
    current_price: float,
    portfolio: dict,
    ml_confidence: float = 0.5,
    gemini_confidence: float = 0.5,
    crisis_detected: bool = False,
) -> RiskAssessment:
    """
    Comprehensive risk assessment for a potential trade.
    Returns a RiskAssessment with all risk checks.
    """
    sector = get_sector(ticker)
    risk_flags = []
    risk_approved = True

    # ── 1. Portfolio Drawdown Check ──────────────────────────────────────
    initial_balance = portfolio.get("initial_balance", settings.initial_balance)
    peak_value = portfolio.get("peak_value", initial_balance)
    total_value = portfolio.get("total_value", initial_balance)

    # Update peak
    if total_value > peak_value:
        peak_value = total_value

    drawdown_pct = 0.0
    if peak_value > 0:
        drawdown_pct = (peak_value - total_value) / peak_value

    if drawdown_pct > settings.max_drawdown_pct:
        risk_flags.append(
            f"DRAWDOWN LIMIT: Portfolio down {drawdown_pct:.1%} from peak "
            f"(limit: {settings.max_drawdown_pct:.0%}). No new BUY allowed."
        )
        risk_approved = False

    # ── 2. Crisis Override ───────────────────────────────────────────────
    if crisis_detected:
        risk_flags.append("CRISIS DETECTED: Market crisis event identified. BUY blocked.")
        risk_approved = False

    # ── 3. Sector Concentration ──────────────────────────────────────────
    holdings = portfolio.get("holdings", [])
    sector_count = sum(
        1 for h in holdings
        if get_sector(h.get("ticker", "")) == sector
        and h.get("ticker") != ticker  # don't count self if adding to position
    )

    if sector_count >= settings.max_sector_stocks:
        risk_flags.append(
            f"SECTOR LIMIT: Already hold {sector_count} stocks in {sector} "
            f"(limit: {settings.max_sector_stocks})"
        )
        risk_approved = False

    # ── 4. Stop-Loss Check (for existing positions) ──────────────────────
    stop_loss_price = None
    trailing_stop_price = None

    existing_holding = None
    for h in holdings:
        if h.get("ticker") == ticker:
            existing_holding = h
            break

    if existing_holding:
        avg_price = existing_holding.get("avg_price", 0)
        peak_price = existing_holding.get("peak_price", avg_price)
        quantity = existing_holding.get("quantity", 0)

        if avg_price > 0 and quantity > 0:
            # Basic stop-loss
            stop_loss_price = avg_price * (1 - settings.stop_loss_pct)

            if current_price <= stop_loss_price:
                risk_flags.append(
                    f"STOP-LOSS HIT: Price Rs.{current_price:.2f} is below "
                    f"stop-loss Rs.{stop_loss_price:.2f} "
                    f"({settings.stop_loss_pct:.0%} below entry Rs.{avg_price:.2f})"
                )

            # Trailing stop (only if position is in profit above threshold)
            gain_pct = (peak_price - avg_price) / avg_price if avg_price > 0 else 0
            if gain_pct >= settings.trailing_stop_activation_pct:
                trailing_stop_price = peak_price * (1 - settings.trailing_stop_distance_pct)

                if current_price <= trailing_stop_price:
                    risk_flags.append(
                        f"TRAILING STOP HIT: Price Rs.{current_price:.2f} is below "
                        f"trailing stop Rs.{trailing_stop_price:.2f} "
                        f"(peak was Rs.{peak_price:.2f})"
                    )

    # ── 5. Position Sizing ───────────────────────────────────────────────
    avg_confidence = (ml_confidence + gemini_confidence) / 2
    base_position_pct = settings.max_position_pct

    # Scale position size by confidence
    # High confidence (>0.8) → full position
    # Medium (0.6-0.8) → 60-100% of max
    # Low (<0.6) → 30-60% of max
    if avg_confidence >= 0.8:
        max_allowed = base_position_pct
    elif avg_confidence >= 0.6:
        max_allowed = base_position_pct * (0.6 + (avg_confidence - 0.6) * 2)
    else:
        max_allowed = base_position_pct * max(0.3, avg_confidence)

    # Reduce position if there are risk flags
    if risk_flags:
        max_allowed *= 0.5

    # ── Compute position risk score ──────────────────────────────────────
    risk_score = 0.0
    if drawdown_pct > 0.05:
        risk_score += 0.3
    if sector_count >= settings.max_sector_stocks - 1:
        risk_score += 0.2
    if crisis_detected:
        risk_score += 0.4
    if not risk_approved:
        risk_score = min(1.0, risk_score + 0.3)

    risk_score = min(1.0, risk_score)

    return RiskAssessment(
        stop_loss_price=round(stop_loss_price, 2) if stop_loss_price else None,
        trailing_stop_price=round(trailing_stop_price, 2) if trailing_stop_price else None,
        position_risk_score=round(risk_score, 3),
        sector=sector,
        sector_exposure_count=sector_count,
        portfolio_drawdown_pct=round(drawdown_pct, 4),
        max_allowed_position_pct=round(max_allowed, 4),
        risk_flags=risk_flags,
        risk_approved=risk_approved,
    )


def check_stop_losses(portfolio: dict, current_prices: Dict[str, float]) -> List[dict]:
    """
    Scan all holdings for stop-loss and trailing stop triggers.
    Returns a list of tickers that should be sold.

    Args:
        portfolio: current portfolio state
        current_prices: {ticker: latest_price} mapping

    Returns:
        List of dicts: [{"ticker": str, "reason": str, "price": float}, ...]
    """
    sell_signals = []
    holdings = portfolio.get("holdings", [])

    for holding in holdings:
        ticker = holding.get("ticker", "")
        avg_price = holding.get("avg_price", 0)
        peak_price = holding.get("peak_price", avg_price)
        quantity = holding.get("quantity", 0)
        current_price = current_prices.get(ticker, 0)

        if not current_price or not avg_price or quantity <= 0:
            continue

        # Check basic stop-loss
        stop_loss_price = avg_price * (1 - settings.stop_loss_pct)
        if current_price <= stop_loss_price:
            sell_signals.append({
                "ticker": ticker,
                "reason": (
                    f"STOP-LOSS: Price Rs.{current_price:.2f} below "
                    f"Rs.{stop_loss_price:.2f} ({settings.stop_loss_pct:.0%} loss)"
                ),
                "price": current_price,
                "trigger": "stop_loss",
            })
            continue

        # Check trailing stop
        gain_pct = (peak_price - avg_price) / avg_price if avg_price > 0 else 0
        if gain_pct >= settings.trailing_stop_activation_pct:
            trailing_stop_price = peak_price * (1 - settings.trailing_stop_distance_pct)
            if current_price <= trailing_stop_price:
                sell_signals.append({
                    "ticker": ticker,
                    "reason": (
                        f"TRAILING STOP: Price Rs.{current_price:.2f} below "
                        f"trailing Rs.{trailing_stop_price:.2f} "
                        f"(peak Rs.{peak_price:.2f})"
                    ),
                    "price": current_price,
                    "trigger": "trailing_stop",
                })

    return sell_signals


def compute_risk_adjustment(risk_assessment: RiskAssessment) -> float:
    """
    Convert risk assessment to a -1 to +1 adjustment factor.
    Positive = favorable risk, negative = elevated risk.
    """
    score = 0.0

    # Base: invert risk score to get risk-adjusted value
    score = 1.0 - (risk_assessment.position_risk_score * 2)

    # If risk not approved, strong negative signal
    if not risk_assessment.risk_approved:
        score = min(score, -0.5)

    # Stop loss hit = strong sell signal
    for flag in risk_assessment.risk_flags:
        if "STOP-LOSS HIT" in flag or "TRAILING STOP HIT" in flag:
            score = -1.0
            break

    return max(-1.0, min(1.0, score))
