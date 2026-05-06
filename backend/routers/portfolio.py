"""
Portfolio API endpoints.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from database import get_portfolio_collection, get_portfolio_history_collection
from ledger import get_portfolio, reset_portfolio
from data_ingestion import get_batch_prices
from news_intelligence import get_sector
from config import settings

router = APIRouter()


class PortfolioResetRequest(BaseModel):
    initial_balance: float = Field(default=settings.initial_balance, gt=0)
    clear_logs: bool = True


@router.get("/portfolio")
async def get_portfolio_state():
    """Get the current portfolio: cash, holdings with live prices, total value, P&L, risk data."""
    try:
        portfolio = await get_portfolio()

        # Refresh holdings with current market prices
        holdings = portfolio.get("holdings", [])
        total_holdings_value = 0.0

        # Batch fetch all live prices at once (faster than one-by-one)
        held_tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]
        live_prices = get_batch_prices(held_tickers) if held_tickers else {}

        # Compute sector allocation
        sector_allocation = {}

        for h in holdings:
            current_price = live_prices.get(h["ticker"]) or h.get("avg_price", 0)
            h["current_price"] = round(current_price, 2)
            h["market_value"] = round(current_price * h["quantity"], 2)
            h["unrealized_pnl"] = round(
                (current_price - h["avg_price"]) * h["quantity"], 2
            )
            h["unrealized_pnl_pct"] = round(
                ((current_price - h["avg_price"]) / h["avg_price"]) * 100, 2
            ) if h["avg_price"] > 0 else 0.0
            total_holdings_value += h["market_value"]

            # Sector info
            sector = h.get("sector") or get_sector(h["ticker"])
            h["sector"] = sector
            sector_allocation[sector] = sector_allocation.get(sector, 0) + h["market_value"]

        total_value = portfolio["cash"] + total_holdings_value
        initial = portfolio.get("initial_balance", settings.initial_balance)
        peak_value = portfolio.get("peak_value", initial)
        if total_value > peak_value:
            peak_value = total_value

        # Compute drawdown
        drawdown_pct = ((peak_value - total_value) / peak_value * 100) if peak_value > 0 else 0.0

        # Remove mongo _id for JSON response
        portfolio.pop("_id", None)

        return {
            **portfolio,
            "holdings": holdings,
            "total_value": round(total_value, 2),
            "holdings_value": round(total_holdings_value, 2),
            "total_pnl": round(total_value - initial, 2),
            "total_pnl_pct": round(((total_value - initial) / initial) * 100, 2),
            "peak_value": round(peak_value, 2),
            "drawdown_pct": round(drawdown_pct, 2),
            "sector_allocation": sector_allocation,
            "risk_status": {
                "drawdown_pct": round(drawdown_pct, 2),
                "drawdown_limit": settings.max_drawdown_pct * 100,
                "buying_halted": drawdown_pct > settings.max_drawdown_pct * 100,
                "stop_loss_pct": settings.stop_loss_pct * 100,
                "max_sector_stocks": settings.max_sector_stocks,
            },
        }

    except Exception as e:
        return {
            "cash": settings.initial_balance,
            "holdings": [],
            "total_value": settings.initial_balance,
            "holdings_value": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "sector_allocation": {},
            "risk_status": {},
        }


@router.post("/portfolio/reset")
async def reset_portfolio_state(payload: PortfolioResetRequest):
    """
    Reset or initialize the paper portfolio to a user-defined virtual capital.
    By default this clears trades, analysis_log, and portfolio_history so P&L restarts at 0.
    """
    try:
        portfolio = await reset_portfolio(
            initial_balance=payload.initial_balance,
            clear_logs=payload.clear_logs,
        )
        return {
            "message": "Portfolio reset",
            "portfolio": portfolio,
            "clear_logs": payload.clear_logs,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
