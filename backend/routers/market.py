"""
Market data API endpoints.
"""

from fastapi import APIRouter, HTTPException
from database import get_market_data_collection
from data_ingestion import build_market_data_doc, ingest_ticker_data, ingest_all_tickers
from config import settings
from nifty_stocks import resolve_watchlist

router = APIRouter()


@router.get("/market/{ticker}")
async def get_market_data(ticker: str):
    """
    Get cached OHLCV + indicator data for a ticker.
    Returns bars for charting and latest indicator values.
    """
    try:
        collection = get_market_data_collection()
        try:
            doc = await collection.find_one({"ticker": ticker}, {"_id": 0})
        except Exception:
            doc = None

        if doc is None or not doc.get("bars"):
            # Try ingesting on-the-fly. This also heals old empty cache docs.
            result = await ingest_ticker_data(ticker)
            if "error" in result:
                raise HTTPException(status_code=404, detail=result["error"])
            try:
                doc = await collection.find_one({"ticker": ticker}, {"_id": 0})
            except Exception:
                doc = None

        if doc is None or not doc.get("bars"):
            doc = build_market_data_doc(ticker)
            doc["cache_status"] = "live_uncached"

        return doc

    except HTTPException:
        raise
    except Exception as e:
        try:
            doc = build_market_data_doc(ticker)
            doc["cache_status"] = "live_uncached"
            return doc
        except Exception:
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
        "watchlist": resolve_watchlist(settings.watchlist),
        "market": "NSE/BSE (India)",
        "timezone": settings.scheduler_timezone,
        "market_hours": f"{settings.market_open_hour}:{settings.market_open_minute:02d} - {settings.market_close_hour}:{settings.market_close_minute:02d} IST",
    }
