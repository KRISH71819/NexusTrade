"""
News Intelligence Engine — multi-level news aggregation with SMART crisis detection.

Sources:
  - NewsData.io (PRIMARY — works from data centers, 200 req/day free)
  - Google News RSS (FALLBACK — may 503 from cloud servers)

Levels:
  MACRO  -> global/India economy, geopolitics, RBI policy
  SECTOR -> banking, IT layoffs, pharma regulations
  STOCK  -> company earnings, management changes, legal issues

Crisis Detection (v2):
  - STOCK-SPECIFIC: only flags crisis if it directly impacts the stock's sector
  - Requires 2+ crisis mentions in relevant news (not just 1 global keyword)
  - Wars/geopolitics only flag Defence, Energy, Metals sectors
  - Generic global conflicts do NOT block all trading
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

# -- Sector mapping for Indian stocks ----------------------------------------

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
    # Misc
    "FSL.NS": "IT", "PRINCEPIPE.NS": "Infra",
}

SECTOR_KEYWORDS: Dict[str, List[str]] = {
    "Banking": ["banking sector india", "RBI policy rate", "bank NPA india"],
    "IT": ["IT sector india", "tech layoffs india", "software exports india"],
    "Pharma": ["pharma sector india", "drug approval india"],
    "Auto": ["auto sales india", "EV india"],
    "Energy": ["crude oil price", "energy sector india", "oil price india"],
    "Metals": ["steel price india", "metal prices"],
    "FMCG": ["FMCG india", "consumer goods india"],
    "Finance": ["NBFC india", "insurance sector india"],
    "Infra": ["infrastructure india", "cement demand india"],
    "Realty": ["real estate india", "housing prices india"],
    "Telecom": ["telecom india", "5G india"],
    "Consumer": ["consumer durables india"],
    "Defence": ["defence orders india", "military spending india"],
    "Healthcare": ["hospital sector india"],
}

MACRO_QUERIES = [
    "India stock market today",
    "Indian economy news",
    "RBI monetary policy",
]

# -- SMART Crisis Detection (v2) -------------------------------------------
# Instead of one big list, map crisis types to affected sectors
# Generic geopolitical conflicts only affect Defence/Energy/Metals

CRISIS_CATEGORIES = {
    "market_wide": {
        "keywords": [
            "market crash", "stock market plunge", "circuit breaker triggered",
            "panic sell", "financial crisis", "banking collapse",
            "global recession confirmed", "india lockdown", "pandemic emergency",
        ],
        "affected_sectors": "ALL",  # blocks all sectors
        "min_matches": 2,  # need 2+ keywords to trigger
    },
    "geopolitical": {
        "keywords": [
            "india pakistan war", "india china military", "nuclear threat india",
            "india border attack", "missile strike india",
        ],
        "affected_sectors": ["Defence", "Energy", "Metals", "Banking"],
        "min_matches": 1,  # direct India conflict = instant flag
    },
    "economic": {
        "keywords": [
            "RBI emergency rate", "rupee crash", "india sovereign default",
            "india sanctions", "FII massive pullout",
        ],
        "affected_sectors": ["Banking", "Finance", "IT"],
        "min_matches": 1,
    },
    "sector_specific": {
        "keywords": [
            "banking crisis india", "IT sector layoffs mass",
            "pharma ban india", "crude oil embargo",
        ],
        "affected_sectors": "MATCH_KEYWORD",  # inferred from the keyword
        "min_matches": 1,
    },
}

# Keywords that are "background noise" — always present, should NOT trigger crisis
NOISE_KEYWORDS = [
    "war", "conflict", "attack", "sanctions", "military",
    "tension", "bomb", "explosion", "terrorist",
    "coup", "martial law",
]


def get_sector(ticker: str) -> str:
    """Get the sector for a given ticker."""
    return SECTOR_MAP.get(ticker, "Unknown")


# ============================================================================
#   NEWSDATA.IO (PRIMARY — works from data centers)
# ============================================================================

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


async def _fetch_newsdata_stock(ticker: str, max_items: int = 5) -> List[NewsItem]:
    """Fetch stock-specific news from NewsData.io."""
    if not settings.newsdata_api_key:
        return []

    stock_name = ticker.replace(".NS", "").replace(".BO", "")
    url = "https://newsdata.io/api/1/latest"
    params = {
        "apikey": settings.newsdata_api_key,
        "q": stock_name,
        "country": "in",
        "language": "en",
        "size": max_items,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        items = []
        for article in data.get("results", [])[:max_items]:
            items.append(NewsItem(
                headline=article.get("title", ""),
                source=article.get("source_name", "NewsData.io"),
                url=article.get("link", ""),
                level=NewsLevel.STOCK,
            ))
        return items
    except Exception as e:
        logger.debug(f"NewsData.io stock search error for {ticker}: {e}")
        return []


# ============================================================================
#   GOOGLE NEWS RSS (FALLBACK — may 503 from data centers)
# ============================================================================

async def _fetch_google_news_rss(query: str, max_items: int = 5) -> List[NewsItem]:
    """Fetch news from Google News RSS feed. Gracefully handles 503 blocks."""
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            if response.status_code == 503:
                logger.debug(f"Google News RSS blocked (503) for '{query}' — using NewsData.io only")
                return []
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
        logger.debug(f"Google News RSS error for '{query}': {e}")
        return []


# ============================================================================
#   SMART CRISIS DETECTION (v2)
# ============================================================================

def detect_crisis(headlines: List[str], sector: str) -> tuple:
    """
    SMART crisis detection — sector-aware, threshold-based.

    Returns (crisis_detected: bool, crisis_reason: str, severity: float)

    Key improvements over v1:
    - Generic wars/conflicts DON'T block all trading
    - Only flags crisis if it directly impacts this stock's sector
    - Requires multiple matches for market-wide events (prevents false positives)
    - Background geopolitical noise is filtered out
    """
    if not headlines:
        return False, "", 0.0

    combined_text = " ".join(h.lower() for h in headlines)

    for category_name, config in CRISIS_CATEGORIES.items():
        matches = []
        for keyword in config["keywords"]:
            if keyword.lower() in combined_text:
                matches.append(keyword)

        if len(matches) < config["min_matches"]:
            continue

        # Check if this crisis affects our sector
        affected = config["affected_sectors"]
        if affected == "ALL":
            severity = min(1.0, 0.3 * len(matches))
            return True, f"Market-wide crisis: {', '.join(matches[:3])}", severity
        elif affected == "MATCH_KEYWORD":
            # Infer sector from keyword
            return True, f"Sector crisis: {', '.join(matches[:3])}", 0.7
        elif sector in affected:
            severity = min(1.0, 0.4 * len(matches))
            return True, f"{category_name} crisis affecting {sector}: {', '.join(matches[:3])}", severity
        else:
            # Crisis exists but doesn't affect this sector
            logger.debug(
                f"Crisis detected ({category_name}: {matches}) but {sector} sector not affected"
            )

    return False, "", 0.0


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

    if not scores:
        return 0.0
    return sum(scores) / len(scores)


# ============================================================================
#   MAIN PIPELINE
# ============================================================================

async def fetch_news_intelligence(
    ticker: str,
    stock_headlines: List[str] = None,
) -> NewsIntelligence:
    """
    Full news intelligence pipeline (deployment-friendly):
    1. Fetch macro news (NewsData.io PRIMARY, Google RSS fallback)
    2. Fetch stock-level news (NewsData.io)
    3. Optionally fetch sector news (Google RSS, may fail on cloud)
    4. SMART crisis detection (sector-aware)
    5. Compute overall news score
    """
    sector = get_sector(ticker)

    # PRIMARY: NewsData.io (works from cloud servers)
    macro_news = await _fetch_newsdata_macro(max_items=8)

    # STOCK NEWS: Try NewsData.io first, then Google RSS as fallback
    stock_news_items = await _fetch_newsdata_stock(ticker, max_items=5)

    # FALLBACK: Google RSS for stock news if NewsData returned nothing
    if not stock_news_items:
        stock_news_items = await _fetch_google_news_rss(
            f"{ticker.replace('.NS', '')} stock NSE",
            max_items=5,
        )
        for item in stock_news_items:
            item.level = NewsLevel.STOCK

    # SECTOR NEWS: Google RSS (optional, may fail on cloud)
    sector_news = []
    if sector in SECTOR_KEYWORDS:
        keyword = SECTOR_KEYWORDS[sector][0]  # just 1 query to save rate limits
        sector_news = await _fetch_google_news_rss(keyword, max_items=3)
        for item in sector_news:
            item.level = NewsLevel.SECTOR

    # Add any pre-fetched stock headlines
    if stock_headlines:
        for h in stock_headlines:
            stock_news_items.append(NewsItem(
                headline=h,
                level=NewsLevel.STOCK,
            ))

    # Deduplicate
    macro_news = _dedupe_news(macro_news)
    sector_news = _dedupe_news(sector_news)
    stock_news_items = _dedupe_news(stock_news_items)

    # SMART crisis detection — sector-aware
    all_headlines = [n.headline for n in macro_news + sector_news + stock_news_items]
    crisis_detected, crisis_reason, crisis_severity = detect_crisis(all_headlines, sector)

    # Mark crisis items
    if crisis_detected:
        for item in macro_news + sector_news + stock_news_items:
            lower = item.headline.lower()
            for cat_config in CRISIS_CATEGORIES.values():
                for kw in cat_config["keywords"]:
                    if kw.lower() in lower:
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
        f"News for {ticker}: macro={len(macro_news)}, sector={len(sector_news)}, "
        f"stock={len(stock_news_items)}, crisis={crisis_detected}, score={overall_score:+.3f}"
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
    """Get cached news intelligence from MongoDB (30-min TTL)."""
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
