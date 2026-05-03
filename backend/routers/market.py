"""
Market data API endpoints.
"""

from fastapi import APIRouter, HTTPException
from database import get_market_data_collection
from data_ingestion import ingest_ticker_data, ingest_all_tickers
from config import settings

router = APIRouter()


@router.get("/market/{ticker}")
async def get_market_data(ticker: str):
    """
    Get cached OHLCV + indicator data for a ticker.
    Returns bars for charting and latest indicator values.
    """
    try:
        collection = get_market_data_collection()
        doc = await collection.find_one({"ticker": ticker}, {"_id": 0})

        if doc is None:
            # Try ingesting on-the-fly
            result = await ingest_ticker_data(ticker)
            if "error" in result:
                raise HTTPException(status_code=404, detail=result["error"])
            doc = await collection.find_one({"ticker": ticker}, {"_id": 0})

        return doc

    except HTTPException:
        raise
    except Exception as e:
        return {"ticker": ticker, "bars": [], "indicators": {}, "news": []}


@router.post("/market/refresh")
async def refresh_market_data():
    """Force a full data refresh for all watchlist tickers."""
    try:
        results = await ingest_all_tickers()
        return {
            "message": "Market data refreshed",
            "tickers_updated": len(results),
            "results": results,
        }
    except Exception as e:
        return {"message": "Error refreshing market data", "tickers_updated": 0, "results": []}


@router.get("/market/watchlist/info")
async def get_watchlist_info():
    """Get the current watchlist configuration."""
    return {
        "watchlist": settings.watchlist,
        "market": "NSE/BSE (India)",
        "timezone": settings.scheduler_timezone,
        "market_hours": f"{settings.market_open_hour}:{settings.market_open_minute:02d} - {settings.market_close_hour}:{settings.market_close_minute:02d} IST",
    }
