"""
Portfolio API endpoints — mode-aware (paper + live).
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from database import get_portfolio_history_collection_for_mode
from ledger import get_portfolio_for_mode, reset_portfolio
from data_ingestion import get_batch_prices
from news_intelligence import get_sector
from config import settings

router = APIRouter()


class PortfolioResetRequest(BaseModel):
    initial_balance: float = Field(default=settings.initial_balance, gt=0)
    clear_logs: bool = True


@router.get("/portfolio")
async def get_portfolio_state(mode: str = Query(default=None)):
    """Get the current portfolio: cash, holdings with live prices, total value, P&L, risk data."""
    try:
        # Use specified mode or current active mode
        active_mode = mode or settings.trading_mode

        portfolio = await get_portfolio_for_mode(active_mode)

        # Refresh holdings with current market prices
        holdings = portfolio.get("holdings", [])
        total_holdings_value = 0.0

        # Batch fetch all live prices at once (faster than one-by-one)
        held_tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]
        live_prices = get_batch_prices(held_tickers) if held_tickers else {}

        # Compute sector allocation
        sector_allocation = {}

        for h in holdings:
            current_price = live_prices.get(h["ticker"]) or h.get("current_price") or h.get("avg_price", 0)
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

        # Get kill switch status
        from kill_switch import is_kill_switch_on
        kill_switch_active = await is_kill_switch_on()

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
            "mode": active_mode,
            "kill_switch_active": kill_switch_active,
            "risk_status": {
                "drawdown_pct": round(drawdown_pct, 2),
                "drawdown_limit": settings.max_drawdown_pct * 100,
                "buying_halted": drawdown_pct > settings.max_drawdown_pct * 100 or kill_switch_active,
                "stop_loss_pct": settings.stop_loss_pct * 100,
                "max_sector_stocks": settings.max_sector_stocks,
                "kill_switch_active": kill_switch_active,
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
            "mode": settings.trading_mode,
        }


@router.post("/portfolio/reset")
async def reset_portfolio_state(payload: PortfolioResetRequest):
    """
    Reset the paper portfolio to a user-defined virtual capital.
    Only works in paper mode — cannot reset a live account!
    """
    if settings.trading_mode == "live":
        raise HTTPException(
            status_code=400,
            detail="Cannot reset portfolio in live mode. Switch to paper mode first."
        )

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
async def get_portfolio_history(limit: int = 100, mode: str = Query(default=None)):
    """Get portfolio value snapshots over time."""
    try:
        active_mode = mode or settings.trading_mode
        collection = get_portfolio_history_collection_for_mode(active_mode)
        cursor = collection.find(
            {}, {"_id": 0}
        ).sort("timestamp", -1).limit(limit)
        snapshots = await cursor.to_list(length=limit)
        # Return in chronological order
        snapshots.reverse()
        return {"snapshots": snapshots, "count": len(snapshots), "mode": active_mode}
    except Exception as e:
        return {"snapshots": [], "count": 0, "mode": settings.trading_mode}
