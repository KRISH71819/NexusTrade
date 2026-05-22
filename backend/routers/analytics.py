"""
Advanced Analytics API endpoints.

Provides:
  - /analytics/pnl      → Daily, Weekly, Yearly P&L (realized + unrealized)
  - /analytics/trade-history → Paginated trade log with per-trade realized PnL
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

from fastapi import APIRouter, Query
from database import (
    get_portfolio_collection,
    get_portfolio_history_collection,
    get_trades_collection,
)
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


# ═══════════════════════════════════════════════════════════════════════════════
#   PnL ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_snapshot_value_at(timestamp: datetime) -> Optional[float]:
    """Get the portfolio total_value closest to (but before) the given timestamp."""
    collection = get_portfolio_history_collection()
    cursor = collection.find(
        {"timestamp": {"$lte": timestamp}},
        {"_id": 0, "total_value": 1, "timestamp": 1},
    ).sort("timestamp", -1).limit(1)
    docs = await cursor.to_list(length=1)
    if docs:
        return docs[0].get("total_value")
    return None


async def _compute_realized_pnl() -> float:
    """
    Compute total realized P&L from all SELL trades.
    For each SELL, realized PnL = (sell_price - avg_buy_price) * quantity.
    We look up the avg_buy_price from the most recent BUY for the same ticker
    that occurred before the sell.
    """
    trades_col = get_trades_collection()

    # Get all sells
    cursor = trades_col.find(
        {"action": "SELL"},
        {"_id": 0, "ticker": 1, "price": 1, "quantity": 1, "timestamp": 1},
    ).sort("timestamp", -1)
    sells = await cursor.to_list(length=10000)

    total_realized = 0.0

    for sell in sells:
        ticker = sell["ticker"]
        sell_price = sell["price"]
        sell_qty = sell["quantity"]
        sell_time = sell["timestamp"]

        # Find the most recent BUY for this ticker before this sell
        buy_cursor = trades_col.find(
            {
                "action": "BUY",
                "ticker": ticker,
                "timestamp": {"$lte": sell_time},
            },
            {"_id": 0, "price": 1, "quantity": 1},
        ).sort("timestamp", -1).limit(1)
        buys = await buy_cursor.to_list(length=1)

        if buys:
            avg_buy_price = buys[0]["price"]
        else:
            # Fallback: try to find any BUY for this ticker
            any_buy_cursor = trades_col.find(
                {"action": "BUY", "ticker": ticker},
                {"_id": 0, "price": 1},
            ).sort("timestamp", -1).limit(1)
            any_buys = await any_buy_cursor.to_list(length=1)
            avg_buy_price = any_buys[0]["price"] if any_buys else sell_price

        realized = (sell_price - avg_buy_price) * sell_qty
        total_realized += realized

    return round(total_realized, 2)


@router.get("/pnl")
async def get_pnl_analytics():
    """
    Compute P&L analytics across multiple timeframes.

    Returns daily, weekly, yearly P&L (from portfolio_history),
    plus total realized and unrealized P&L.
    """
    try:
        now = datetime.now(timezone.utc)

        # Get current portfolio state
        portfolio_col = get_portfolio_collection()
        portfolio = await portfolio_col.find_one({"_id": "main"})

        if not portfolio:
            return _empty_pnl_response()

        current_value = portfolio.get("total_value", settings.initial_balance)
        initial_balance = portfolio.get("initial_balance", settings.initial_balance)
        cash = portfolio.get("cash", 0)
        holdings = portfolio.get("holdings", [])

        # Compute unrealized PnL from current holdings
        total_unrealized = 0.0
        for h in holdings:
            qty = h.get("quantity", 0)
            avg_price = h.get("avg_price", 0)
            current_price = h.get("current_price", avg_price)
            if qty > 0 and avg_price > 0:
                total_unrealized += (current_price - avg_price) * qty
        total_unrealized = round(total_unrealized, 2)

        # Compute realized PnL
        total_realized = await _compute_realized_pnl()

        # ── Timeframe PnL (from portfolio_history snapshots) ─────────────
        day_ago_value = await _get_snapshot_value_at(now - timedelta(days=1))
        week_ago_value = await _get_snapshot_value_at(now - timedelta(days=7))
        year_ago_value = await _get_snapshot_value_at(now - timedelta(days=365))

        # Fallback to initial balance if no snapshot exists
        day_base = day_ago_value or initial_balance
        week_base = week_ago_value or initial_balance
        year_base = year_ago_value or initial_balance

        daily_pnl = round(current_value - day_base, 2)
        weekly_pnl = round(current_value - week_base, 2)
        yearly_pnl = round(current_value - year_base, 2)

        return {
            "daily_pnl": {
                "value": daily_pnl,
                "pct": round((daily_pnl / day_base) * 100, 2) if day_base > 0 else 0.0,
            },
            "weekly_pnl": {
                "value": weekly_pnl,
                "pct": round((weekly_pnl / week_base) * 100, 2) if week_base > 0 else 0.0,
            },
            "yearly_pnl": {
                "value": yearly_pnl,
                "pct": round((yearly_pnl / year_base) * 100, 2) if year_base > 0 else 0.0,
            },
            "total_realized_pnl": total_realized,
            "total_unrealized_pnl": total_unrealized,
            "total_portfolio_value": round(current_value, 2),
            "cash": round(cash, 2),
            "initial_balance": round(initial_balance, 2),
            "total_pnl": round(current_value - initial_balance, 2),
            "total_pnl_pct": round(
                ((current_value - initial_balance) / initial_balance) * 100, 2
            ) if initial_balance > 0 else 0.0,
        }

    except Exception as e:
        logger.error(f"PnL analytics failed: {e}", exc_info=True)
        return _empty_pnl_response()


def _empty_pnl_response():
    return {
        "daily_pnl": {"value": 0.0, "pct": 0.0},
        "weekly_pnl": {"value": 0.0, "pct": 0.0},
        "yearly_pnl": {"value": 0.0, "pct": 0.0},
        "total_realized_pnl": 0.0,
        "total_unrealized_pnl": 0.0,
        "total_portfolio_value": settings.initial_balance,
        "cash": settings.initial_balance,
        "initial_balance": settings.initial_balance,
        "total_pnl": 0.0,
        "total_pnl_pct": 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   TRADE HISTORY WITH REALIZED PnL
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/trade-history")
async def get_trade_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ticker: Optional[str] = None,
):
    """
    Paginated trade history with realized P&L for each SELL trade.

    For each SELL, we compute:
      realized_pnl = (sell_price - avg_buy_price) * quantity
      realized_pnl_pct = ((sell_price - avg_buy_price) / avg_buy_price) * 100

    BUY trades show realized_pnl = null.
    """
    try:
        trades_col = get_trades_collection()

        # Build query
        query = {}
        if ticker:
            query["ticker"] = {"$regex": f"^{ticker}", "$options": "i"}

        # Get total count
        total_count = await trades_col.count_documents(query)

        # Paginate
        skip = (page - 1) * page_size
        cursor = trades_col.find(
            query, {"_id": 0}
        ).sort("timestamp", -1).skip(skip).limit(page_size)
        raw_trades = await cursor.to_list(length=page_size)

        # Enrich SELL trades with realized PnL
        enriched = []
        for trade in raw_trades:
            enriched_trade = {
                "timestamp": trade.get("timestamp"),
                "ticker": trade.get("ticker", ""),
                "action": trade.get("action", ""),
                "quantity": trade.get("quantity", 0),
                "price": trade.get("price", 0),
                "total_value": trade.get("total_value", 0),
                "final_score": trade.get("final_score", 0),
                "ml_confidence": trade.get("ml_confidence", 0),
                "gemini_confidence": trade.get("gemini_confidence", 0),
                "gemini_sentiment_score": trade.get("gemini_sentiment_score", 0),
                "ai_reasoning": trade.get("action_reason", "") or trade.get("gemini_explanation", ""),
                "crisis_detected": trade.get("crisis_detected", False),
                "realized_pnl": None,
                "realized_pnl_pct": None,
                "portfolio_snapshot": trade.get("portfolio_snapshot"),
            }

            if trade.get("action") == "SELL":
                t = trade["ticker"]
                sell_price = trade["price"]
                sell_qty = trade["quantity"]
                sell_time = trade.get("timestamp")

                # Look up matching BUY price
                buy_cursor = trades_col.find(
                    {
                        "action": "BUY",
                        "ticker": t,
                        "timestamp": {"$lte": sell_time} if sell_time else {},
                    },
                    {"_id": 0, "price": 1},
                ).sort("timestamp", -1).limit(1)
                buys = await buy_cursor.to_list(length=1)

                if buys:
                    avg_buy_price = buys[0]["price"]
                    realized = round((sell_price - avg_buy_price) * sell_qty, 2)
                    realized_pct = round(
                        ((sell_price - avg_buy_price) / avg_buy_price) * 100, 2
                    ) if avg_buy_price > 0 else 0.0
                    enriched_trade["realized_pnl"] = realized
                    enriched_trade["realized_pnl_pct"] = realized_pct

            enriched.append(enriched_trade)

        return {
            "trades": enriched,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_count + page_size - 1) // page_size),
        }

    except Exception as e:
        logger.error(f"Trade history failed: {e}", exc_info=True)
        return {
            "trades": [],
            "total_count": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
        }
