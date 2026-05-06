"""
News Intelligence API endpoints.
"""

from fastapi import APIRouter
from news_intelligence import fetch_news_intelligence, get_cached_news, get_sector
from database import get_db

router = APIRouter()


@router.get("/news/latest")
async def get_latest_news():
    """
    Get the most recent cached news intelligence across all analyzed tickers.
    Returns macro, sector, and stock-level news with crisis alerts.
    """
    try:
        db = get_db()
        cursor = db["news_intelligence"].find(
            {}, {"_id": 0}
        ).sort("fetched_at", -1).limit(20)
        docs = await cursor.to_list(length=20)

        # Extract unique macro news from the most recent entry
        macro_news = []
        crisis_alerts = []
        if docs:
            latest = docs[0]
            macro_news = latest.get("macro_news", [])
            if latest.get("crisis_detected"):
                crisis_alerts.append({
                    "ticker": latest.get("ticker", ""),
                    "reason": latest.get("crisis_reason", ""),
                })

        # Collect all crisis alerts
        for doc in docs:
            if doc.get("crisis_detected"):
                crisis_alerts.append({
                    "ticker": doc.get("ticker", ""),
                    "reason": doc.get("crisis_reason", ""),
                })

        return {
            "macro_news": macro_news[:10],
            "ticker_news": [
                {
                    "ticker": d.get("ticker", ""),
                    "sector": d.get("sector", ""),
                    "stock_news": d.get("stock_news", [])[:3],
                    "sector_news": d.get("sector_news", [])[:2],
                    "overall_news_score": d.get("overall_news_score", 0),
                    "crisis_detected": d.get("crisis_detected", False),
                }
                for d in docs
            ],
            "crisis_alerts": crisis_alerts,
            "total_tickers": len(docs),
        }

    except Exception as e:
        return {
            "macro_news": [],
            "ticker_news": [],
            "crisis_alerts": [],
            "total_tickers": 0,
            "error": str(e),
        }


@router.get("/news/{ticker}")
async def get_ticker_news(ticker: str):
    """Get news intelligence for a specific ticker."""
    try:
        cached = await get_cached_news(ticker)
        if cached:
            return cached

        # Fetch fresh if not cached
        intel = await fetch_news_intelligence(ticker)
        return intel.model_dump()

    except Exception as e:
        return {
            "macro_news": [],
            "sector_news": [],
            "stock_news": [],
            "crisis_detected": False,
            "error": str(e),
        }
