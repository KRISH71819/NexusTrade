"""
Analysis log API endpoints — the "AI Brain" data.
"""

from fastapi import APIRouter, HTTPException, Query
from database import get_analysis_collection, get_trades_collection

router = APIRouter()


@router.get("/analysis/latest")
async def get_latest_analyses():
    """
    Get the most recent analysis result for each ticker in the watchlist.
    This powers the 'AI Brain' panel on the dashboard.
    """
    try:
        collection = get_analysis_collection()
        trades_collection = get_trades_collection()

        # Aggregate: latest analysis per ticker
        pipeline = [
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id": "$ticker",
                "latest": {"$first": "$$ROOT"},
            }},
            {"$replaceRoot": {"newRoot": "$latest"}},
            {"$project": {"_id": 0}},
            {"$sort": {"timestamp": -1}},
        ]
        cursor = await collection.aggregate(pipeline)
        analyses = await cursor.to_list(length=100)

        # Also pull latest BUY/SELL trades for context
        trade_pipeline = [
            {"$match": {"action": {"$in": ["BUY", "SELL"]}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id": "$ticker",
                "latest_trade": {"$first": "$$ROOT"},
            }},
            {"$replaceRoot": {"newRoot": "$latest_trade"}},
            {"$project": {"_id": 0}},
        ]
        trade_cursor = await trades_collection.aggregate(trade_pipeline)
        latest_trades = await trade_cursor.to_list(length=100)
        trade_map = {t["ticker"]: t for t in latest_trades}

        # Merge trade info into analysis
        for a in analyses:
            ticker = a.get("ticker", "")
            a["latest_trade"] = trade_map.get(ticker)
            # Normalize field names for frontend
            a["price"] = a.get("current_price", 0)

        return {"analyses": analyses, "count": len(analyses)}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"analyses": [], "count": 0}


@router.get("/analysis/{ticker}")
async def get_analysis_history(
    ticker: str,
    limit: int = Query(50, ge=1, le=200),
):
    """Full analysis history for a specific ticker."""
    try:
        collection = get_analysis_collection()

        query = {"ticker": {"$regex": f"^{ticker}", "$options": "i"}}
        cursor = collection.find(
            query, {"_id": 0}
        ).sort("timestamp", -1).limit(limit)
        analyses = await cursor.to_list(length=limit)

        return {"analyses": analyses, "total_count": len(analyses), "ticker": ticker}

    except Exception as e:
        return {"analyses": [], "total_count": 0, "ticker": ticker}
