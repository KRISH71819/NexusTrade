"""
News Intelligence Engine — multi-level news aggregation with crisis detection.

Sources:
  - Google News RSS (free, no API key) — macro + sector + stock
  - NewsData.io (free tier, 200 req/day) — India-specific macro
  - yfinance + Finnhub (existing) — stock-specific fallback

Levels:
  MACRO  → global/India economy, geopolitics, wars, RBI policy
  SECTOR → banking crisis, IT layoffs, pharma regulations
  STOCK  → company earnings, management changes, legal issues
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from urllib.parse import quote_plus

import feedparser
import httpx

from config import settings
from models import NewsItem, NewsIntelligence, NewsLevel, NewsSentiment
from database import get_db

logger = logging.getLogger(__name__)

# ── Sector mapping for Indian stocks ─────────────────────────────────────────

SECTOR_MAP: Dict[str, str] = {
    # Banking & Finance
    "HDFCBANK.NS": "Banking", "ICICIBANK.NS": "Banking", "KOTAKBANK.NS": "Banking",
    "SBIN.NS": "Banking", "AXISBANK.NS": "Banking", "INDUSINDBK.NS": "Banking",
    "BANKBARODA.NS": "Banking", "PNB.NS": "Banking", "CANBK.NS": "Banking",
    "IDFCFIRSTB.NS": "Banking", "FEDERALBNK.NS": "Banking", "BANDHANBNK.NS": "Banking",
    "AUBANK.NS": "Banking", "RBLBANK.NS": "Banking", "IDBI.NS": "Banking",
    "BAJFINANCE.NS": "Finance", "BAJAJFINSV.NS": "Finance", "CHOLAFIN.NS": "Finance",
    "SHRIRAMFIN.NS": "Finance", "M&MFIN.NS": "Finance", "MUTHOOTFIN.NS": "Finance",
    "MANAPPURAM.NS": "Finance", "LICHSGFIN.NS": "Finance", "POONAWALLA.NS": "Finance",
    "HDFCAMC.NS": "Finance", "SBICARD.NS": "Finance", "SBILIFE.NS": "Finance",
    "HDFCLIFE.NS": "Finance", "ICICIPRULI.NS": "Finance", "ICICIGI.NS": "Finance",
    # IT
    "TCS.NS": "IT", "INFY.NS": "IT", "WIPRO.NS": "IT", "HCLTECH.NS": "IT",
    "TECHM.NS": "IT", "LTIM.NS": "IT", "LTTS.NS": "IT", "COFORGE.NS": "IT",
    "MPHASIS.NS": "IT", "PERSISTENT.NS": "IT", "KPITTECH.NS": "IT",
    "NAUKRI.NS": "IT", "CYIENT.NS": "IT", "HAPPSTMNDS.NS": "IT",
    # Pharma
    "SUNPHARMA.NS": "Pharma", "DRREDDY.NS": "Pharma", "CIPLA.NS": "Pharma",
    "DIVISLAB.NS": "Pharma", "AUROPHARMA.NS": "Pharma", "LUPIN.NS": "Pharma",
    "BIOCON.NS": "Pharma", "TORNTPHARM.NS": "Pharma", "ALKEM.NS": "Pharma",
    "ZYDUSLIFE.NS": "Pharma", "IPCALAB.NS": "Pharma", "GLENMARK.NS": "Pharma",
    # Auto
    "MARUTI.NS": "Auto", "M&M.NS": "Auto", "TATAMOTORS.NS": "Auto",
    "BAJAJ-AUTO.NS": "Auto", "HEROMOTOCO.NS": "Auto", "EICHERMOT.NS": "Auto",
    "ASHOKLEY.NS": "Auto", "ESCORTS.NS": "Auto", "APOLLOTYRE.NS": "Auto",
    # Energy & Oil
    "RELIANCE.NS": "Energy", "ONGC.NS": "Energy", "BPCL.NS": "Energy",
    "IOC.NS": "Energy", "HINDPETRO.NS": "Energy", "GAIL.NS": "Energy",
    "COALINDIA.NS": "Energy", "NTPC.NS": "Energy", "POWERGRID.NS": "Energy",
    "ADANIGREEN.NS": "Energy", "ADANIPOWER.NS": "Energy", "ADANIENT.NS": "Energy",
    "TATAPOWER.NS": "Energy", "JSWENERGY.NS": "Energy",
    # Metals & Mining
    "TATASTEEL.NS": "Metals", "JSWSTEEL.NS": "Metals", "HINDALCO.NS": "Metals",
    "VEDL.NS": "Metals", "JINDALSTEL.NS": "Metals", "NMDC.NS": "Metals",
    "SAIL.NS": "Metals", "HINDCOPPER.NS": "Metals", "HINDZINC.NS": "Metals",
    # FMCG
    "HINDUNILVR.NS": "FMCG", "ITC.NS": "FMCG", "NESTLEIND.NS": "FMCG",
    "BRITANNIA.NS": "FMCG", "GODREJCP.NS": "FMCG", "MARICO.NS": "FMCG",
    "COLPAL.NS": "FMCG", "EMAMILTD.NS": "FMCG", "DMART.NS": "FMCG",
    # Infra & Real Estate
    "LT.NS": "Infra", "ADANIPORTS.NS": "Infra", "ULTRACEMCO.NS": "Infra",
    "AMBUJACEM.NS": "Infra", "SHREECEM.NS": "Infra", "ACC.NS": "Infra",
    "GODREJPROP.NS": "Realty", "OBEROIRLTY.NS": "Realty", "PRESTIGE.NS": "Realty",
    "LODHA.NS": "Realty", "BRIGADE.NS": "Realty",
    # Telecom
    "BHARTIARTL.NS": "Telecom", "IDEA.NS": "Telecom",
    # Consumer
    "TITAN.NS": "Consumer", "TRENT.NS": "Consumer", "PAGEIND.NS": "Consumer",
    "ASIANPAINT.NS": "Consumer", "BERGEPAINT.NS": "Consumer", "PIDILITIND.NS": "Consumer",
    "BATAINDIA.NS": "Consumer", "HAVELLS.NS": "Consumer", "VOLTAS.NS": "Consumer",
    "CROMPTON.NS": "Consumer", "DIXON.NS": "Consumer", "POLYCAB.NS": "Consumer",
    # Defence
    "BEL.NS": "Defence", "BDL.NS": "Defence", "BEML.NS": "Defence",
    "COCHINSHIP.NS": "Defence", "MAZDOCK.NS": "Defence",
    # Healthcare
    "APOLLOHOSP.NS": "Healthcare", "FORTIS.NS": "Healthcare",
    "MAXHEALTH.NS": "Healthcare", "MEDANTA.NS": "Healthcare",
}

SECTOR_KEYWORDS: Dict[str, List[str]] = {
    "Banking": ["banking sector india", "RBI policy rate", "bank NPA india", "credit growth india"],
    "IT": ["IT sector india", "tech layoffs india", "software exports india", "NASSCOM"],
    "Pharma": ["pharma sector india", "drug approval india", "FDA india pharma"],
    "Auto": ["auto sales india", "EV india", "automobile sector india"],
    "Energy": ["crude oil price", "energy sector india", "oil price india", "OPEC"],
    "Metals": ["steel price india", "metal prices", "commodity metals india"],
    "FMCG": ["FMCG india", "consumer goods india", "rural demand india"],
    "Finance": ["NBFC india", "insurance sector india", "fintech india"],
    "Infra": ["infrastructure india", "cement demand india", "construction india"],
    "Realty": ["real estate india", "housing prices india", "property market india"],
    "Telecom": ["telecom india", "5G india", "TRAI"],
    "Consumer": ["consumer durables india", "retail sector india"],
    "Defence": ["defence orders india", "military spending india"],
    "Healthcare": ["hospital sector india", "healthcare india"],
}

MACRO_QUERIES = [
    "India stock market",
    "Indian economy",
    "RBI monetary policy",
    "India geopolitics",
    "global markets crash",
    "US Federal Reserve",
    "crude oil price impact",
    "India China tension",
    "India Pakistan",
    "global recession risk",
]

CRISIS_KEYWORDS = [
    "war", "military strike", "attack", "invasion", "conflict escalat",
    "market crash", "stock market plunge", "circuit breaker", "panic sell",
    "recession", "financial crisis", "banking collapse", "default",
    "sanctions", "embargo", "trade war",
    "terrorist", "bomb", "explosion",
    "pandemic", "lockdown", "emergency",
    "coup", "martial law",
    "nuclear", "missile",
]


def get_sector(ticker: str) -> str:
    """Get the sector for a given ticker."""
    return SECTOR_MAP.get(ticker, "Unknown")


# ═══════════════════════════════════════════════════════════════════════════════
#   GOOGLE NEWS RSS (free, no API key)
# ═══════════════════════════════════════════════════════════════════════════════

async def _fetch_google_news_rss(query: str, max_items: int = 5) -> List[NewsItem]:
    """Fetch news from Google News RSS feed."""
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()

        feed = feedparser.parse(response.text)
        items = []
        for entry in feed.entries[:max_items]:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass

            items.append(NewsItem(
                headline=entry.get("title", ""),
                source=entry.get("source", {}).get("title", "Google News") if isinstance(entry.get("source"), dict) else "Google News",
                url=entry.get("link", ""),
                published_at=published,
            ))
        return items
    except Exception as e:
        logger.warning(f"Google News RSS error for '{query}': {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#   NEWSDATA.IO (India macro news)
# ═══════════════════════════════════════════════════════════════════════════════

async def _fetch_newsdata_macro(max_items: int = 10) -> List[NewsItem]:
    """Fetch India macro news from NewsData.io free API."""
    if not settings.newsdata_api_key:
        logger.warning("NewsData.io API key not configured")
        return []

    url = "https://newsdata.io/api/1/latest"
    params = {
        "apikey": settings.newsdata_api_key,
        "country": "in",
        "category": "business",
        "language": "en",
        "size": max_items,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        items = []
        for article in data.get("results", [])[:max_items]:
            published = None
            pub_date = article.get("pubDate")
            if pub_date:
                try:
                    published = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                except Exception:
                    pass

            items.append(NewsItem(
                headline=article.get("title", ""),
                source=article.get("source_name", "NewsData.io"),
                url=article.get("link", ""),
                published_at=published,
                level=NewsLevel.MACRO,
            ))
        logger.info(f"Fetched {len(items)} macro headlines from NewsData.io")
        return items
    except Exception as e:
        logger.warning(f"NewsData.io error: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#   CRISIS DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_crisis(headlines: List[str]) -> tuple[bool, str]:
    """
    Scan headlines for crisis keywords.
    Returns (crisis_detected, reason).
    """
    for headline in headlines:
        lower = headline.lower()
        for keyword in CRISIS_KEYWORDS:
            if keyword in lower:
                return True, f"Crisis keyword '{keyword}' detected in: {headline[:100]}"
    return False, ""


def compute_news_score(news_items: List[NewsItem]) -> float:
    """Compute aggregate news impact score from classified items."""
    if not news_items:
        return 0.0

    scores = []
    for item in news_items:
        if item.sentiment == NewsSentiment.CRISIS:
            scores.append(-0.9)
        elif item.sentiment == NewsSentiment.BEARISH:
            scores.append(-0.5)
        elif item.sentiment == NewsSentiment.BULLISH:
            scores.append(0.5)
        else:
            scores.append(0.0)

    # Weight more recent news higher
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_news_intelligence(
    ticker: str,
    stock_headlines: List[str] = None,
) -> NewsIntelligence:
    """
    Full news intelligence pipeline:
    1. Fetch macro news (Google News RSS + NewsData.io)
    2. Fetch sector news (Google News RSS by sector)
    3. Combine with stock-level news
    4. Detect crises
    5. Compute overall news score
    """
    sector = get_sector(ticker)

    # Fetch all levels concurrently
    macro_tasks = [
        _fetch_google_news_rss(query, max_items=3)
        for query in MACRO_QUERIES[:4]  # limit to avoid rate limits
    ]
    macro_tasks.append(_fetch_newsdata_macro(max_items=8))

    sector_tasks = []
    if sector in SECTOR_KEYWORDS:
        for keyword in SECTOR_KEYWORDS[sector][:2]:
            sector_tasks.append(_fetch_google_news_rss(keyword, max_items=3))

    stock_task = _fetch_google_news_rss(
        f"{ticker.replace('.NS', '')} stock NSE",
        max_items=5,
    )

    all_results = await asyncio.gather(
        *macro_tasks, *sector_tasks, stock_task,
        return_exceptions=True,
    )

    # Separate results
    macro_end = len(macro_tasks)
    sector_end = macro_end + len(sector_tasks)

    macro_news = []
    for result in all_results[:macro_end]:
        if isinstance(result, list):
            for item in result:
                item.level = NewsLevel.MACRO
                macro_news.append(item)

    sector_news = []
    for result in all_results[macro_end:sector_end]:
        if isinstance(result, list):
            for item in result:
                item.level = NewsLevel.SECTOR
                sector_news.append(item)

    stock_news_items = []
    stock_result = all_results[-1]
    if isinstance(stock_result, list):
        for item in stock_result:
            item.level = NewsLevel.STOCK
            stock_news_items.append(item)

    # Add any pre-fetched stock headlines
    if stock_headlines:
        for h in stock_headlines:
            stock_news_items.append(NewsItem(
                headline=h,
                level=NewsLevel.STOCK,
            ))

    # Deduplicate by headline similarity
    macro_news = _dedupe_news(macro_news)
    sector_news = _dedupe_news(sector_news)
    stock_news_items = _dedupe_news(stock_news_items)

    # Crisis detection across all levels
    all_headlines = [n.headline for n in macro_news + sector_news + stock_news_items]
    crisis_detected, crisis_reason = detect_crisis(all_headlines)

    # Mark crisis items
    if crisis_detected:
        for item in macro_news + sector_news + stock_news_items:
            lower = item.headline.lower()
            for kw in CRISIS_KEYWORDS:
                if kw in lower:
                    item.sentiment = NewsSentiment.CRISIS
                    item.impact_score = -0.9
                    break

    overall_score = compute_news_score(macro_news + sector_news + stock_news_items)

    intelligence = NewsIntelligence(
        macro_news=macro_news[:10],
        sector_news=sector_news[:6],
        stock_news=stock_news_items[:6],
        crisis_detected=crisis_detected,
        crisis_reason=crisis_reason,
        overall_news_score=round(overall_score, 3),
        fetched_at=datetime.now(timezone.utc),
    )

    # Cache to MongoDB
    try:
        db = get_db()
        await db["news_intelligence"].update_one(
            {"ticker": ticker},
            {"$set": {
                "ticker": ticker,
                "sector": sector,
                **intelligence.model_dump(),
            }},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"Could not cache news intelligence: {e}")

    logger.info(
        f"News intelligence for {ticker}: "
        f"macro={len(macro_news)}, sector={len(sector_news)}, "
        f"stock={len(stock_news_items)}, crisis={crisis_detected}, "
        f"score={overall_score:+.3f}"
    )

    return intelligence


def _dedupe_news(items: List[NewsItem], threshold: float = 0.7) -> List[NewsItem]:
    """Remove near-duplicate headlines using simple word overlap."""
    if not items:
        return []

    unique = [items[0]]
    for item in items[1:]:
        words_new = set(item.headline.lower().split())
        is_dupe = False
        for existing in unique:
            words_existing = set(existing.headline.lower().split())
            if not words_new or not words_existing:
                continue
            overlap = len(words_new & words_existing) / max(len(words_new), len(words_existing))
            if overlap > threshold:
                is_dupe = True
                break
        if not is_dupe:
            unique.append(item)
    return unique


async def get_cached_news(ticker: str) -> Optional[dict]:
    """Get cached news intelligence from MongoDB."""
    try:
        db = get_db()
        doc = await db["news_intelligence"].find_one(
            {"ticker": ticker},
            {"_id": 0},
        )
        if doc:
            fetched = doc.get("fetched_at")
            if fetched and isinstance(fetched, datetime):
                age = datetime.now(timezone.utc) - fetched.replace(tzinfo=timezone.utc)
                if age < timedelta(minutes=30):
                    return doc
        return None
    except Exception:
        return None
