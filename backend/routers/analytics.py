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
    get_portfolio_collection_for_mode,
    get_portfolio_history_collection_for_mode,
    get_trades_collection_for_mode,
)
from data_ingestion import get_batch_prices
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["Analytics"])

# IST offset for market-open anchoring
_IST_OFFSET = timedelta(hours=5, minutes=30)


# ═══════════════════════════════════════════════════════════════════════════════
#   PnL ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

def _market_open_today_utc(now_utc: datetime) -> datetime:
    """
    Get today's Indian market open time (9:15 IST) in UTC.
    If current time is before 9:15 IST, returns yesterday's 9:15 IST.
    """
    now_ist = now_utc + _IST_OFFSET
    market_open_ist = now_ist.replace(
        hour=settings.market_open_hour,
        minute=settings.market_open_minute,
        second=0, microsecond=0,
    )
    if now_ist < market_open_ist:
        # Before today's market open — use previous day's open
        market_open_ist -= timedelta(days=1)
    return market_open_ist - _IST_OFFSET  # convert back to UTC


def _week_start_utc(now_utc: datetime) -> datetime:
    """
    Get this week's Monday 9:15 IST in UTC.
    If current day is before Monday market open, go to previous week's Monday.
    """
    now_ist = now_utc + _IST_OFFSET
    # Monday = 0 in weekday()
    days_since_monday = now_ist.weekday()
    monday_ist = (now_ist - timedelta(days=days_since_monday)).replace(
        hour=settings.market_open_hour,
        minute=settings.market_open_minute,
        second=0, microsecond=0,
    )
    if now_ist < monday_ist:
        monday_ist -= timedelta(weeks=1)
    return monday_ist - _IST_OFFSET  # convert back to UTC


async def _get_snapshot_value_at(timestamp: datetime, mode: str = "paper") -> Optional[float]:
    """Get the portfolio total_value closest to (but before) the given timestamp."""
    collection = get_portfolio_history_collection_for_mode(mode)
    cursor = collection.find(
        {"timestamp": {"$lte": timestamp}},
        {"_id": 0, "total_value": 1, "timestamp": 1},
    ).sort("timestamp", -1).limit(1)
    docs = await cursor.to_list(length=1)
    if docs:
        return docs[0].get("total_value")
    return None


async def _compute_total_charges(mode: str = "paper") -> float:
    """Sum all charges paid across all trades."""
    trades_col = get_trades_collection_for_mode(mode)
    cursor = trades_col.find(
        {"charges.total_charges": {"$exists": True}},
        {"_id": 0, "charges.total_charges": 1},
    )
    total = 0.0
    async for doc in cursor:
        total += doc.get("charges", {}).get("total_charges", 0)
    return round(total, 2)


@router.get("/pnl")
async def get_pnl_analytics(mode: str = Query(default=None)):
    """
    Compute P&L analytics across multiple timeframes.

    Key principles:
      1. Uses LIVE market prices for current_value (not stale DB value)
      2. Computes realized_pnl = total_pnl - unrealized_pnl (guarantees they sum correctly)
      3. Daily P&L = change since market open today (9:15 IST)
      4. Weekly P&L = change since Monday 9:15 IST
    """
    try:
        active_mode = mode or settings.trading_mode
        now = datetime.now(timezone.utc)

        # Get current portfolio state
        portfolio_col = get_portfolio_collection_for_mode(active_mode)
        portfolio = await portfolio_col.find_one({"_id": "main"})

        if not portfolio:
            return _empty_pnl_response()

        initial_balance = portfolio.get("initial_balance", settings.initial_balance)
        cash = portfolio.get("cash", 0)
        holdings = portfolio.get("holdings", [])

        # ── LIVE VALUATION: fetch live prices for all holdings ────────────
        held_tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]
        live_prices = get_batch_prices(held_tickers) if held_tickers else {}

        # Compute holdings value and unrealized P&L using LIVE prices
        holdings_value = 0.0
        total_unrealized = 0.0
        invested_capital = 0.0
        for h in holdings:
            qty = h.get("quantity", 0)
            avg_price = h.get("avg_price", 0)
            current_price = live_prices.get(h["ticker"]) or h.get("current_price", avg_price)

            market_value = current_price * qty
            holdings_value += market_value

            if qty > 0 and avg_price > 0:
                total_unrealized += (current_price - avg_price) * qty
                invested_capital += avg_price * qty

        total_unrealized = round(total_unrealized, 2)
        invested_capital = round(invested_capital, 2)

        # Current value using LIVE prices (same formula as /api/portfolio)
        current_value = round(cash + holdings_value, 2)

        # Total P&L from initial balance
        total_pnl = round(current_value - initial_balance, 2)
        total_pnl_pct = round(
            ((current_value - initial_balance) / initial_balance) * 100, 2
        ) if initial_balance > 0 else 0.0

        # ── REALIZED P&L: computed residually so it ALWAYS adds up ───────
        # total_pnl = realized + unrealized, therefore:
        total_realized = round(total_pnl - total_unrealized, 2)

        # ── DAILY P&L: change since market open today (9:15 IST) ─────────
        day_anchor = _market_open_today_utc(now)
        day_base_value = await _get_snapshot_value_at(day_anchor, active_mode)

        if day_base_value is not None:
            daily_pnl = round(current_value - day_base_value, 2)
            daily_pnl_pct = round(
                (daily_pnl / day_base_value) * 100, 2
            ) if day_base_value > 0 else 0.0
        else:
            # No snapshot exists before market open — likely first day
            daily_pnl = 0.0
            daily_pnl_pct = 0.0

        # ── WEEKLY P&L: change since Monday 9:15 IST ────────────────────
        week_anchor = _week_start_utc(now)
        week_base_value = await _get_snapshot_value_at(week_anchor, active_mode)

        if week_base_value is not None:
            weekly_pnl = round(current_value - week_base_value, 2)
            weekly_pnl_pct = round(
                (weekly_pnl / week_base_value) * 100, 2
            ) if week_base_value > 0 else 0.0
        else:
            weekly_pnl = 0.0
            weekly_pnl_pct = 0.0

        # ── YEARLY P&L ──────────────────────────────────────────────────
        year_ago_value = await _get_snapshot_value_at(now - timedelta(days=365), active_mode)
        if year_ago_value is not None:
            yearly_pnl = round(current_value - year_ago_value, 2)
            yearly_pnl_pct = round(
                (yearly_pnl / year_ago_value) * 100, 2
            ) if year_ago_value > 0 else 0.0
        else:
            yearly_pnl = total_pnl
            yearly_pnl_pct = total_pnl_pct

        # ── TOTAL CHARGES PAID ──────────────────────────────────────────
        total_charges = await _compute_total_charges(active_mode)

        return {
            "daily_pnl": {
                "value": daily_pnl,
                "pct": daily_pnl_pct,
            },
            "weekly_pnl": {
                "value": weekly_pnl,
                "pct": weekly_pnl_pct,
            },
            "yearly_pnl": {
                "value": yearly_pnl,
                "pct": yearly_pnl_pct,
            },
            "total_realized_pnl": total_realized,
            "total_unrealized_pnl": total_unrealized,
            "total_portfolio_value": current_value,
            "cash": round(cash, 2),
            "initial_balance": round(initial_balance, 2),
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "invested_capital": invested_capital,
            "total_charges_paid": total_charges,
            "mode": active_mode,
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
        "invested_capital": 0.0,
        "total_charges_paid": 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   TRADE HISTORY WITH REALIZED PnL
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/trade-history")
async def get_trade_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ticker: Optional[str] = None,
    mode: str = Query(default=None),
):
    """
    Paginated trade history with realized P&L for each SELL trade.

    For each SELL, we compute:
      realized_pnl = (sell_price - avg_buy_price) * quantity
      realized_pnl_pct = ((sell_price - avg_buy_price) / avg_buy_price) * 100

    BUY trades show realized_pnl = null.
    """
    try:
        active_mode = mode or settings.trading_mode if hasattr(settings, 'trading_mode') else "paper"
        trades_col = get_trades_collection_for_mode(active_mode)

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
                "charges": trade.get("charges"),
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
