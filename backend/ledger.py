"""
Virtual Trading Ledger — manages paper-trading portfolio operations.
Every trade is logged with full AI brain transparency data.
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
            "cash": 10000.0,
            "total_value": 10000.0,
            "holdings": [],
            "initial_balance": 10000.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0
        }


async def get_portfolio_value() -> float:
    """Get total portfolio value (cash + holdings)."""
    portfolio = await get_portfolio()
    return portfolio["total_value"]


async def get_holding(ticker: str) -> Optional[dict]:
    """Get holding for a specific ticker, or None if not held."""
    portfolio = await get_portfolio()
    for h in portfolio.get("holdings", []):
        if h["ticker"] == ticker:
            return h
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#   TRADE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

async def execute_buy(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    quantity: Optional[int] = None,
) -> dict:
    """
    Execute a virtual BUY order.
    If quantity is None, auto-size based on max_position_pct.
    """
    portfolio = await get_portfolio()
    cash = portfolio["cash"]

    # Position sizing: max 20% of total portfolio value
    max_spend = portfolio["total_value"] * settings.max_position_pct
    available = min(cash, max_spend)

    if available < price:
        logger.warning(f"Insufficient funds to buy {ticker} at ₹{price:.2f}")
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
    else:
        holdings.append({
            "ticker": ticker,
            "quantity": quantity,
            "avg_price": round(price, 2),
        })

    # Compute new total value
    holdings_value = sum(h["quantity"] * h.get("avg_price", 0) for h in holdings)
    total_value = new_cash + holdings_value

    # Update portfolio in DB
    try:
        collection = get_portfolio_collection()
        await collection.update_one(
            {"_id": "main"},
            {"$set": {
                "cash": round(new_cash, 2),
                "holdings": holdings,
                "total_value": round(total_value, 2),
                "total_pnl": round(total_value - portfolio["initial_balance"], 2),
                "total_pnl_pct": round(
                    ((total_value - portfolio["initial_balance"]) / portfolio["initial_balance"]) * 100, 2
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

        # Record portfolio snapshot
        await _record_snapshot(new_cash, holdings_value, total_value)
    except PyMongoError as e:
        logger.warning(f"Could not log BUY trade to MongoDB: {e}")
        trade_doc = _build_trade_doc(
            ticker=ticker, action=TradeAction.BUY, quantity=quantity, price=price,
            total_value=total_cost, analysis=analysis, cash_after=new_cash, portfolio_total=total_value,
        )

    logger.info(
        f"BUY {quantity}x {ticker} @ ₹{price:.2f} = ₹{total_cost:.2f} | "
        f"Cash remaining: ₹{new_cash:.2f}"
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

    # Update portfolio in DB
    try:
        collection = get_portfolio_collection()
        await collection.update_one(
            {"_id": "main"},
            {"$set": {
                "cash": round(new_cash, 2),
                "holdings": holdings,
                "total_value": round(total_value, 2),
                "total_pnl": round(total_value - portfolio["initial_balance"], 2),
                "total_pnl_pct": round(
                    ((total_value - portfolio["initial_balance"]) / portfolio["initial_balance"]) * 100, 2
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

        # Record snapshot
        await _record_snapshot(new_cash, holdings_value, total_value)
    except PyMongoError as e:
        logger.warning(f"Could not log SELL trade to MongoDB: {e}")
        trade_doc = _build_trade_doc(
            ticker=ticker, action=TradeAction.SELL, quantity=quantity, price=price,
            total_value=total_proceeds, analysis=analysis, cash_after=new_cash, portfolio_total=total_value,
        )

    logger.info(
        f"SELL {quantity}x {ticker} @ ₹{price:.2f} = ₹{total_proceeds:.2f} | "
        f"Cash now: ₹{new_cash:.2f}"
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
        "action_reason": analysis.action_reason,
    }
    try:
        await get_analysis_collection().insert_one(analysis_doc)
    except PyMongoError as e:
        logger.warning(f"Could not log HOLD decision to MongoDB: {e}")

    logger.info(
        f"HOLD {ticker} — ML: {analysis.ml_confidence:.2f}, "
        f"Sentiment: {analysis.gemini_sentiment_score:.2f}"
    )

    return analysis_doc


# ═══════════════════════════════════════════════════════════════════════════════
#   HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

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
