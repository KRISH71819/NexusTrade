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


# ============================================================================
#   UNDERPERFORMER DETECTION — sell stagnant/declining stocks
# ============================================================================

def detect_underperformers(
    portfolio: dict,
    current_prices: Dict[str, float],
) -> List[dict]:
    """
    Detect holdings that are underperforming and should be sold.

    Checks:
    1. SLOW BLEED: Stock has been losing > 3% over the last 5 days
       (not enough for stop-loss, but steadily declining)
    2. STAGNANT: Stock has barely moved (< 1%) in 5 days — dead money
       that could be better deployed elsewhere
    3. NEGATIVE MOMENTUM: Stock is below its entry price AND has negative
       recent trend (not recovering)

    Returns list of sell signals with reason and recommended action.
    """
    sell_signals = []
    holdings = portfolio.get("holdings", [])

    for holding in holdings:
        ticker = holding.get("ticker", "")
        avg_price = holding.get("avg_price", 0)
        quantity = holding.get("quantity", 0)
        current_price = current_prices.get(ticker, 0)

        if not current_price or not avg_price or quantity <= 0:
            continue

        # Current P&L
        pnl_pct = (current_price - avg_price) / avg_price if avg_price > 0 else 0

        # Get recent price history to check trend
        try:
            import yfinance as yf
            hist = yf.download(
                ticker,
                period=f"{settings.underperformer_days + 2}d",
                interval="1d",
                progress=False,
            )

            if hist.empty or len(hist) < 2:
                continue

            # Handle MultiIndex columns from yfinance
            if hasattr(hist.columns, 'levels'):
                close_col = hist["Close"]
                if hasattr(close_col, 'columns'):
                    close_col = close_col.iloc[:, 0]
            else:
                close_col = hist["Close"]

            price_n_days_ago = float(close_col.iloc[0])
            price_latest = float(close_col.iloc[-1])

            if price_n_days_ago <= 0:
                continue

            recent_change_pct = (price_latest - price_n_days_ago) / price_n_days_ago

        except Exception as e:
            logger.debug(f"Could not fetch history for {ticker}: {e}")
            # Fall back to just using current P&L
            recent_change_pct = pnl_pct

        # ── Check 1: SLOW BLEED ──────────────────────────────────────────
        # Stock dropping steadily but not enough to trigger stop-loss
        if recent_change_pct < -settings.underperformer_min_loss_pct:
            sell_signals.append({
                "ticker": ticker,
                "reason": (
                    f"SLOW BLEED: {ticker} declined {recent_change_pct:.1%} "
                    f"over {settings.underperformer_days} days "
                    f"(P&L: {pnl_pct:+.1%} from entry Rs.{avg_price:.2f})"
                ),
                "price": current_price,
                "trigger": "underperformer_bleed",
                "sell_all": True,  # full sell — declining stock
                "pnl_pct": round(pnl_pct, 4),
                "recent_change_pct": round(recent_change_pct, 4),
            })
            continue

        # ── Check 2: STAGNANT (dead money) ───────────────────────────────
        # Stock hasn't moved — capital is locked doing nothing
        if (abs(recent_change_pct) < settings.underperformer_stagnant_pct
                and pnl_pct < 0.02):  # only flag if not already profitable
            sell_signals.append({
                "ticker": ticker,
                "reason": (
                    f"STAGNANT: {ticker} moved only {recent_change_pct:+.1%} "
                    f"in {settings.underperformer_days} days — "
                    f"dead money (P&L: {pnl_pct:+.1%})"
                ),
                "price": current_price,
                "trigger": "underperformer_stagnant",
                "sell_all": True,  # free up capital
                "pnl_pct": round(pnl_pct, 4),
                "recent_change_pct": round(recent_change_pct, 4),
            })
            continue

        # ── Check 3: NEGATIVE MOMENTUM with loss ─────────────────────────
        # Below entry AND still dropping — not recovering
        if pnl_pct < -0.02 and recent_change_pct < -0.005:
            sell_signals.append({
                "ticker": ticker,
                "reason": (
                    f"NEGATIVE MOMENTUM: {ticker} is {pnl_pct:+.1%} from entry "
                    f"and still declining ({recent_change_pct:+.1%} recent). "
                    f"Not recovering."
                ),
                "price": current_price,
                "trigger": "underperformer_momentum",
                "sell_all": True,
                "pnl_pct": round(pnl_pct, 4),
                "recent_change_pct": round(recent_change_pct, 4),
            })

    if sell_signals:
        logger.info(
            f"Underperformer scan found {len(sell_signals)} weak holdings: "
            f"{[s['ticker'] for s in sell_signals]}"
        )

    return sell_signals


# ============================================================================
#   PROFIT-TAKING — partial sell on big winners to lock in gains
# ============================================================================

def check_profit_taking(
    portfolio: dict,
    current_prices: Dict[str, float],
) -> List[dict]:
    """
    Check for holdings that have gained enough to warrant partial profit-taking.

    When a stock is up 20%+ from entry, sell 25% of the position to:
    - Lock in real gains
    - Reduce risk exposure
    - Free up capital for new opportunities

    Only triggers ONCE per threshold crossing (tracked via peak_price).
    """
    sell_signals = []
    holdings = portfolio.get("holdings", [])

    for holding in holdings:
        ticker = holding.get("ticker", "")
        avg_price = holding.get("avg_price", 0)
        quantity = holding.get("quantity", 0)
        current_price = current_prices.get(ticker, 0)

        if not current_price or not avg_price or quantity <= 1:
            continue

        gain_pct = (current_price - avg_price) / avg_price if avg_price > 0 else 0

        # Only take profit if gain exceeds threshold
        if gain_pct >= settings.profit_take_threshold_pct:
            # Calculate shares to sell (25% of position, minimum 1)
            shares_to_sell = max(1, int(quantity * settings.profit_take_partial_pct))

            # Don't sell everything — keep at least 1 share
            if shares_to_sell >= quantity:
                shares_to_sell = max(1, quantity - 1)

            profit_value = shares_to_sell * current_price
            profit_realized = shares_to_sell * (current_price - avg_price)

            sell_signals.append({
                "ticker": ticker,
                "reason": (
                    f"PROFIT TAKING: {ticker} up {gain_pct:.1%} "
                    f"(Rs.{avg_price:.2f} → Rs.{current_price:.2f}). "
                    f"Selling {shares_to_sell}/{quantity} shares to lock "
                    f"Rs.{profit_realized:.2f} profit"
                ),
                "price": current_price,
                "trigger": "profit_taking",
                "sell_all": False,
                "sell_quantity": shares_to_sell,
                "pnl_pct": round(gain_pct, 4),
                "profit_realized": round(profit_realized, 2),
            })

    if sell_signals:
        logger.info(
            f"Profit-taking scan found {len(sell_signals)} winners: "
            f"{[s['ticker'] for s in sell_signals]}"
        )

    return sell_signals
