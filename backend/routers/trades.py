"""
Trade history API endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from database import get_trades_collection

router = APIRouter()


@router.get("/trades")
async def get_all_trades(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get all trades with full transparency data, newest first."""
    try:
        collection = get_trades_collection()

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
        }

    except Exception as e:
        return {
            "trades": [],
            "total_count": 0,
            "limit": limit,
            "offset": offset,
        }


@router.get("/trades/{ticker}")
async def get_trades_by_ticker(ticker: str, limit: int = Query(50, ge=1, le=500)):
    """Get all trades for a specific ticker."""
    try:
        collection = get_trades_collection()

        # Support both with and without .NS suffix
        query = {"ticker": {"$regex": f"^{ticker}", "$options": "i"}}
        cursor = collection.find(
            query, {"_id": 0}
        ).sort("timestamp", -1).limit(limit)
        trades = await cursor.to_list(length=limit)

        return {"trades": trades, "total_count": len(trades), "ticker": ticker}

    except Exception as e:
        return {"trades": [], "total_count": 0, "ticker": ticker}
