"""
Portfolio API endpoints.
"""

from fastapi import APIRouter, HTTPException
from database import get_portfolio_collection, get_portfolio_history_collection
from ledger import get_portfolio
from data_ingestion import get_latest_price

router = APIRouter()


@router.get("/portfolio")
async def get_portfolio_state():
    """Get the current portfolio: cash, holdings, total value, P&L."""
    try:
        portfolio = await get_portfolio()

        # Refresh holdings with current market prices
        holdings = portfolio.get("holdings", [])
        total_holdings_value = 0.0

        for h in holdings:
            current_price = get_latest_price(h["ticker"])
            if current_price:
                h["current_price"] = round(current_price, 2)
                h["market_value"] = round(current_price * h["quantity"], 2)
                h["unrealized_pnl"] = round(
                    (current_price - h["avg_price"]) * h["quantity"], 2
                )
                h["unrealized_pnl_pct"] = round(
                    ((current_price - h["avg_price"]) / h["avg_price"]) * 100, 2
                ) if h["avg_price"] > 0 else 0.0
                total_holdings_value += h["market_value"]
            else:
                h["current_price"] = h["avg_price"]
                h["market_value"] = round(h["avg_price"] * h["quantity"], 2)
                h["unrealized_pnl"] = 0.0
                h["unrealized_pnl_pct"] = 0.0
                total_holdings_value += h["market_value"]

        total_value = portfolio["cash"] + total_holdings_value
        initial = portfolio.get("initial_balance", 10000.0)

        # Remove mongo _id for JSON response
        portfolio.pop("_id", None)

        return {
            **portfolio,
            "holdings": holdings,
            "total_value": round(total_value, 2),
            "holdings_value": round(total_holdings_value, 2),
            "total_pnl": round(total_value - initial, 2),
            "total_pnl_pct": round(((total_value - initial) / initial) * 100, 2),
        }

    except Exception as e:
        return {
            "cash": 10000.0,
            "holdings": [],
            "total_value": 10000.0,
            "holdings_value": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
        }


@router.get("/portfolio/history")
async def get_portfolio_history(limit: int = 100):
    """Get portfolio value snapshots over time."""
    try:
        collection = get_portfolio_history_collection()
        cursor = collection.find(
            {}, {"_id": 0}
        ).sort("timestamp", -1).limit(limit)
        snapshots = await cursor.to_list(length=limit)
        # Return in chronological order
        snapshots.reverse()
        return {"snapshots": snapshots, "count": len(snapshots)}
    except Exception as e:
        return {"snapshots": [], "count": 0}
