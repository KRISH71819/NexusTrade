"""
Virtual Trading Ledger — manages paper-trading portfolio operations.
Every trade is logged with full AI brain transparency data.

Key improvements:
  - Uses LIVE market prices for holdings valuation (not avg_price)
  - Tracks peak_price per holding for trailing stops
  - Tracks portfolio peak_value for drawdown calculation
"""

from datetime import datetime, timezone
from typing import Optional
import logging
from pymongo.errors import PyMongoError

from database import (
    get_portfolio_collection,
    get_trades_collection,
    get_analysis_collection,
    get_portfolio_history_collection,
)
from models import TradeAction, AnalysisResult, Portfolio, Holding
from config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#   READ OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def get_portfolio() -> dict:
    """Retrieve the current portfolio state."""
    try:
        collection = get_portfolio_collection()
        doc = await collection.find_one({"_id": "main"})
        if doc is None:
            raise RuntimeError("Portfolio not initialized")
        return doc
    except PyMongoError as e:
        logger.warning(f"MongoDB offline: {e}. Returning mock portfolio.")
        return {
            "cash": settings.initial_balance,
            "total_value": settings.initial_balance,
            "holdings": [],
            "initial_balance": settings.initial_balance,
            "peak_value": settings.initial_balance,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
        }


async def get_portfolio_value() -> float:
    """Get total portfolio value (cash + holdings at market price)."""
    portfolio = await get_portfolio()
    return portfolio["total_value"]


async def reset_portfolio(initial_balance: float, clear_logs: bool = True) -> dict:
    """
    Reset the paper-trading account to a clean starting balance.
    This lets the dashboard/user restart the shadow portfolio at 0% profit.
    """
    if initial_balance <= 0:
        raise ValueError("initial_balance must be greater than 0")

    now = datetime.now(timezone.utc)
    portfolio_doc = {
        "_id": "main",
        "cash": round(initial_balance, 2),
        "holdings": [],
        "total_value": round(initial_balance, 2),
        "holdings_value": 0.0,
        "total_pnl": 0.0,
        "total_pnl_pct": 0.0,
        "initial_balance": round(initial_balance, 2),
        "peak_value": round(initial_balance, 2),
        "created_at": now,
        "updated_at": now,
    }

    collection = get_portfolio_collection()
    await collection.replace_one({"_id": "main"}, portfolio_doc, upsert=True)

    if clear_logs:
        await get_trades_collection().delete_many({})
        await get_analysis_collection().delete_many({})
        await get_portfolio_history_collection().delete_many({})

    await _record_snapshot(initial_balance, 0.0, initial_balance)
    logger.info(f"Portfolio reset with starting balance Rs {initial_balance:,.2f}")

    portfolio_doc.pop("_id", None)
    return portfolio_doc


async def get_holding(ticker: str) -> Optional[dict]:
    """Get holding for a specific ticker, or None if not held."""
    portfolio = await get_portfolio()
    for h in portfolio.get("holdings", []):
        if h["ticker"] == ticker:
            return h
    return None


async def has_position(ticker: str) -> bool:
    """Check if we currently hold a position in the ticker."""
    holding = await get_holding(ticker)
    return holding is not None and holding.get("quantity", 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
#   PORTFOLIO VALUATION (using live prices)
# ═══════════════════════════════════════════════════════════════════════════════

async def update_portfolio_valuation(live_prices: dict) -> dict:
    """
    Update portfolio total_value using live market prices.
    Also updates peak_price per holding for trailing stops.

    Args:
        live_prices: {ticker: current_price} mapping
    """
    portfolio = await get_portfolio()
    holdings = portfolio.get("holdings", [])
    cash = portfolio["cash"]

    holdings_value = 0.0
    for h in holdings:
        ticker = h["ticker"]
        current_price = live_prices.get(ticker) or h.get("avg_price", 0)
        qty = h.get("quantity", 0)
        avg_price = h.get("avg_price", 0)

        market_value = current_price * qty
        holdings_value += market_value

        # Update live valuation fields on the holding
        h["current_price"] = round(current_price, 2)
        h["market_value"] = round(market_value, 2)
        h["unrealized_pnl"] = round((current_price - avg_price) * qty, 2) if avg_price > 0 else 0.0
        h["unrealized_pnl_pct"] = round(
            ((current_price - avg_price) / avg_price) * 100, 2
        ) if avg_price > 0 else 0.0

        # Update peak_price for trailing stop (always track the highest price seen)
        peak = h.get("peak_price", avg_price or current_price)
        if current_price > peak:
            h["peak_price"] = round(current_price, 2)
        elif peak == 0:
            # Ensure peak_price is never 0
            h["peak_price"] = round(max(current_price, avg_price), 2)

    total_value = cash + holdings_value
    initial = portfolio.get("initial_balance", settings.initial_balance)

    # Update peak portfolio value
    peak_value = portfolio.get("peak_value", initial)
    if total_value > peak_value:
        peak_value = total_value

    try:
        collection = get_portfolio_collection()
        await collection.update_one(
            {"_id": "main"},
            {"$set": {
                "holdings": holdings,
                "total_value": round(total_value, 2),
                "holdings_value": round(holdings_value, 2),
                "peak_value": round(peak_value, 2),
                "total_pnl": round(total_value - initial, 2),
                "total_pnl_pct": round(
                    ((total_value - initial) / initial) * 100, 2
                ) if initial > 0 else 0.0,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
    except PyMongoError as e:
        logger.warning(f"Could not update portfolio valuation: {e}")

    return {
        "cash": cash,
        "holdings_value": round(holdings_value, 2),
        "total_value": round(total_value, 2),
        "peak_value": round(peak_value, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   TRADE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

async def execute_buy(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    quantity: Optional[int] = None,
    max_position_pct: Optional[float] = None,
) -> dict:
    """
    Execute a virtual BUY order.
    If quantity is None, auto-size based on max_position_pct.
    """
    portfolio = await get_portfolio()
    cash = portfolio["cash"]

    # ── Guardrail 1: Max open positions (diversification) ───────────────
    holdings_list = portfolio.get("holdings", [])
    holdings_count = len([h for h in holdings_list if h.get("quantity", 0) > 0])
    is_existing = any(h["ticker"] == ticker for h in holdings_list if h.get("quantity", 0) > 0)
    if not is_existing and holdings_count >= settings.max_open_positions:
        logger.warning(
            f"MAX POSITIONS: Already holding {holdings_count}/{settings.max_open_positions} stocks. "
            f"Cannot open new position in {ticker}."
        )
        return {"error": "Max positions reached", "ticker": ticker}

    # ── Guardrail 2: Tiny cash reserve (5% emergency buffer) ─────────
    min_reserve = portfolio["total_value"] * settings.min_cash_reserve_pct
    spendable_cash = max(0, cash - min_reserve)

    # ── Guardrail 3: Per-trade diversification cap ───────────────────
    position_pct = max_position_pct or settings.max_position_pct
    trade_cap = settings.max_single_trade_pct
    effective_pct = min(position_pct, trade_cap)
    max_spend = portfolio["total_value"] * effective_pct
    available = min(spendable_cash, max_spend)

    if available < price:
        logger.warning(
            f"Insufficient funds to buy {ticker} at Rs.{price:.2f} "
            f"(available: Rs.{available:.2f}, reserve: Rs.{min_reserve:.2f})"
        )
        return {"error": "Insufficient funds", "ticker": ticker}

    if quantity is None:
        quantity = int(available // price)

    if quantity <= 0:
        return {"error": "Computed quantity is 0", "ticker": ticker}

    total_cost = quantity * price
    new_cash = cash - total_cost

    # Update holdings
    holdings = portfolio.get("holdings", [])
    existing = None
    for h in holdings:
        if h["ticker"] == ticker:
            existing = h
            break

    if existing:
        # Average up
        total_qty = existing["quantity"] + quantity
        avg_price = (
            (existing["avg_price"] * existing["quantity"]) + (price * quantity)
        ) / total_qty
        existing["quantity"] = total_qty
        existing["avg_price"] = round(avg_price, 2)
        # Update peak price
        existing["peak_price"] = max(existing.get("peak_price", 0), price)
        # Update bought_at to latest buy time (resets underperformer grace period)
        existing["bought_at"] = datetime.now(timezone.utc)
    else:
        from news_intelligence import get_sector
        holdings.append({
            "ticker": ticker,
            "quantity": quantity,
            "avg_price": round(price, 2),
            "peak_price": round(price, 2),
            "sector": get_sector(ticker),
            "bought_at": datetime.now(timezone.utc),
        })

    # Compute new total value using live price for all holdings
    holdings_value = sum(h["quantity"] * price if h["ticker"] == ticker
                        else h["quantity"] * h.get("avg_price", 0)
                        for h in holdings)
    total_value = new_cash + holdings_value
    initial = portfolio.get("initial_balance", settings.initial_balance)

    # Update peak
    peak_value = max(portfolio.get("peak_value", initial), total_value)

    # Update portfolio in DB
    try:
        collection = get_portfolio_collection()
        await collection.update_one(
            {"_id": "main"},
            {"$set": {
                "cash": round(new_cash, 2),
                "holdings": holdings,
                "total_value": round(total_value, 2),
                "holdings_value": round(holdings_value, 2),
                "peak_value": round(peak_value, 2),
                "total_pnl": round(total_value - initial, 2),
                "total_pnl_pct": round(
                    ((total_value - initial) / initial) * 100, 2
                ),
                "updated_at": datetime.now(timezone.utc),
            }},
        )

        # Log the trade with full transparency
        trade_doc = _build_trade_doc(
            ticker=ticker,
            action=TradeAction.BUY,
            quantity=quantity,
            price=price,
            total_value=total_cost,
            analysis=analysis,
            cash_after=new_cash,
            portfolio_total=total_value,
        )
        await get_trades_collection().insert_one(trade_doc)
        await _log_analysis_decision(ticker, TradeAction.BUY, analysis)

        # Record portfolio snapshot
        await _record_snapshot(new_cash, holdings_value, total_value)
    except PyMongoError as e:
        logger.warning(f"Could not log BUY trade to MongoDB: {e}")
        trade_doc = _build_trade_doc(
            ticker=ticker, action=TradeAction.BUY, quantity=quantity, price=price,
            total_value=total_cost, analysis=analysis, cash_after=new_cash, portfolio_total=total_value,
        )

    logger.info(
        f"BUY {quantity}x {ticker} @ Rs.{price:.2f} = Rs.{total_cost:.2f} | "
        f"Cash remaining: Rs.{new_cash:.2f}"
    )

    return trade_doc


async def execute_sell(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    quantity: Optional[int] = None,
) -> dict:
    """
    Execute a virtual SELL order.
    If quantity is None, sell the entire position.
    """
    portfolio = await get_portfolio()
    holdings = portfolio.get("holdings", [])

    existing = None
    for h in holdings:
        if h["ticker"] == ticker:
            existing = h
            break

    if not existing or existing["quantity"] <= 0:
        logger.warning(f"No position to sell for {ticker}")
        return {"error": "No position", "ticker": ticker}

    if quantity is None:
        quantity = existing["quantity"]

    quantity = min(quantity, existing["quantity"])
    total_proceeds = quantity * price

    # Update holding
    existing["quantity"] -= quantity
    if existing["quantity"] <= 0:
        holdings = [h for h in holdings if h["ticker"] != ticker]

    new_cash = portfolio["cash"] + total_proceeds
    holdings_value = sum(h["quantity"] * h.get("avg_price", 0) for h in holdings)
    total_value = new_cash + holdings_value
    initial = portfolio.get("initial_balance", settings.initial_balance)

    # Update peak
    peak_value = max(portfolio.get("peak_value", initial), total_value)

    # Update portfolio in DB
    try:
        collection = get_portfolio_collection()
        await collection.update_one(
            {"_id": "main"},
            {"$set": {
                "cash": round(new_cash, 2),
                "holdings": holdings,
                "total_value": round(total_value, 2),
                "holdings_value": round(holdings_value, 2),
                "peak_value": round(peak_value, 2),
                "total_pnl": round(total_value - initial, 2),
                "total_pnl_pct": round(
                    ((total_value - initial) / initial) * 100, 2
                ),
                "updated_at": datetime.now(timezone.utc),
            }},
        )

        # Log trade
        trade_doc = _build_trade_doc(
            ticker=ticker,
            action=TradeAction.SELL,
            quantity=quantity,
            price=price,
            total_value=total_proceeds,
            analysis=analysis,
            cash_after=new_cash,
            portfolio_total=total_value,
        )
        await get_trades_collection().insert_one(trade_doc)
        await _log_analysis_decision(ticker, TradeAction.SELL, analysis)

        # Record snapshot
        await _record_snapshot(new_cash, holdings_value, total_value)
    except PyMongoError as e:
        logger.warning(f"Could not log SELL trade to MongoDB: {e}")
        trade_doc = _build_trade_doc(
            ticker=ticker, action=TradeAction.SELL, quantity=quantity, price=price,
            total_value=total_proceeds, analysis=analysis, cash_after=new_cash, portfolio_total=total_value,
        )

    logger.info(
        f"SELL {quantity}x {ticker} @ Rs.{price:.2f} = Rs.{total_proceeds:.2f} | "
        f"Cash now: Rs.{new_cash:.2f}"
    )

    return trade_doc


async def log_hold(ticker: str, analysis: AnalysisResult) -> dict:
    """Log a HOLD decision (no trade) with full analysis for transparency."""
    analysis_doc = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc),
        "action": TradeAction.HOLD.value,
        "current_price": analysis.current_price,
        "ml_confidence": analysis.ml_confidence,
        "ml_features_used": analysis.ml_features_used,
        "news_headlines": analysis.news_headlines,
        "gemini_sentiment_score": analysis.gemini_sentiment_score,
        "gemini_explanation": analysis.gemini_explanation,
        "gemini_confidence": analysis.gemini_confidence,
        "final_score": analysis.final_score,
        "crisis_detected": analysis.crisis_detected,
        "action_reason": analysis.action_reason,
    }
    try:
        await get_analysis_collection().insert_one(analysis_doc)
    except PyMongoError as e:
        logger.warning(f"Could not log HOLD decision to MongoDB: {e}")

    logger.info(
        f"HOLD {ticker} — Score: {analysis.final_score:.2f}, "
        f"ML: {analysis.ml_confidence:.2f}, "
        f"Gemini: {analysis.gemini_confidence:.2f}"
    )

    return analysis_doc


# ═══════════════════════════════════════════════════════════════════════════════
#   HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def _log_analysis_decision(
    ticker: str,
    action: TradeAction,
    analysis: AnalysisResult,
) -> dict:
    """Log every AI decision, including BUY/SELL/HOLD, to analysis_log."""
    analysis_doc = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc),
        "action": action.value,
        "current_price": analysis.current_price,
        "ml_confidence": analysis.ml_confidence,
        "ml_features_used": analysis.ml_features_used,
        "news_headlines": analysis.news_headlines,
        "gemini_sentiment_score": analysis.gemini_sentiment_score,
        "gemini_explanation": analysis.gemini_explanation,
        "gemini_confidence": analysis.gemini_confidence,
        "gemini_risk_factors": analysis.gemini_risk_factors,
        "final_score": analysis.final_score,
        "crisis_detected": analysis.crisis_detected,
        "action_reason": analysis.action_reason,
    }
    try:
        await get_analysis_collection().insert_one(analysis_doc)
    except PyMongoError as e:
        logger.warning(f"Could not log {action.value} decision to MongoDB: {e}")

    return analysis_doc


def _build_trade_doc(
    ticker: str,
    action: TradeAction,
    quantity: int,
    price: float,
    total_value: float,
    analysis: AnalysisResult,
    cash_after: float,
    portfolio_total: float,
) -> dict:
    """Build a fully transparent trade document."""
    return {
        "timestamp": datetime.now(timezone.utc),
        "ticker": ticker,
        "action": action.value,
        "quantity": quantity,
        "price": round(price, 2),
        "total_value": round(total_value, 2),
        # Full AI brain transparency
        "ml_confidence": analysis.ml_confidence,
        "news_headlines": analysis.news_headlines,
        "gemini_sentiment_score": analysis.gemini_sentiment_score,
        "gemini_explanation": analysis.gemini_explanation,
        "gemini_confidence": analysis.gemini_confidence,
        "final_score": analysis.final_score,
        "crisis_detected": analysis.crisis_detected,
        "action_reason": analysis.action_reason,
        # Portfolio state after trade
        "portfolio_snapshot": {
            "timestamp": datetime.now(timezone.utc),
            "cash": round(cash_after, 2),
            "holdings_value": round(portfolio_total - cash_after, 2),
            "total_value": round(portfolio_total, 2),
        },
    }


async def _record_snapshot(cash: float, holdings_value: float, total_value: float):
    """Record a portfolio value snapshot for historical tracking."""
    await get_portfolio_history_collection().insert_one({
        "timestamp": datetime.now(timezone.utc),
        "cash": round(cash, 2),
        "holdings_value": round(holdings_value, 2),
        "total_value": round(total_value, 2),
    })
