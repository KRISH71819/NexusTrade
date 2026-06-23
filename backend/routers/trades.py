"""
Trade history API endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from database import get_trades_collection_for_mode
from config import settings

router = APIRouter()


@router.get("/trades")
async def get_all_trades(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    mode: str = Query(default=None),
):
    """Get all trades with full transparency data, newest first."""
    try:
        active_mode = mode or settings.trading_mode
        collection = get_trades_collection_for_mode(active_mode)

        total = await collection.count_documents({})
        cursor = collection.find(
            {}, {"_id": 0}
        ).sort("timestamp", -1).skip(offset).limit(limit)
        trades = await cursor.to_list(length=limit)

        return {
            "trades": trades,
            "total_count": total,
            "limit": limit,
            "offset": offset,
            "mode": active_mode,
        }

    except Exception as e:
        return {
            "trades": [],
            "total_count": 0,
            "limit": limit,
            "offset": offset,
            "mode": settings.trading_mode,
        }


@router.get("/trades/{ticker}")
async def get_trades_by_ticker(ticker: str, limit: int = Query(50, ge=1, le=500), mode: str = Query(default=None)):
    """Get all trades for a specific ticker."""
    try:
        active_mode = mode or settings.trading_mode
        collection = get_trades_collection_for_mode(active_mode)

        # Support both with and without .NS suffix
        query = {"ticker": {"$regex": f"^{ticker}", "$options": "i"}}
        cursor = collection.find(
            query, {"_id": 0}
        ).sort("timestamp", -1).limit(limit)
        trades = await cursor.to_list(length=limit)

        return {"trades": trades, "total_count": len(trades), "ticker": ticker, "mode": active_mode}

    except Exception as e:
        return {"trades": [], "total_count": 0, "ticker": ticker, "mode": settings.trading_mode}
