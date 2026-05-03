"""
Data Ingestion Engine — pulls market data via yfinance and news via
yfinance (primary) / Finnhub (fallback).  Computes technical indicators.
Targeted at NSE/BSE Indian stocks.
"""

import yfinance as yf
import finnhub
import pandas as pd
import numpy as np
from ta.trend import SMAIndicator, EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import logging

from config import settings
from models import NewsItem, OHLCVBar
from database import get_market_data_collection

logger = logging.getLogger(__name__)

# ── Finnhub client (lazy init) ───────────────────────────────────────────────
_finnhub_client: finnhub.Client | None = None


def _get_finnhub_client() -> finnhub.Client:
    global _finnhub_client
    if _finnhub_client is None:
        _finnhub_client = finnhub.Client(api_key=settings.finnhub_api_key)
    return _finnhub_client


# ═══════════════════════════════════════════════════════════════════════════════
#   MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv(ticker: str, period: str = "60d", interval: str = "1h") -> pd.DataFrame:
    """
    Pull OHLCV data from yfinance.
    Ticker should already include .NS suffix for NSE stocks.
    """
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=period, interval=interval)

        if df.empty:
            logger.warning(f"No OHLCV data returned for {ticker}")
            return pd.DataFrame()

        # Normalize column names
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        df = df.reset_index()

        # Ensure we have a datetime column
        if "date" in df.columns:
            df = df.rename(columns={"date": "datetime"})
        elif "Datetime" in df.columns:
            df = df.rename(columns={"Datetime": "datetime"})

        logger.info(f"Fetched {len(df)} bars for {ticker} ({interval})")
        return df

    except Exception as e:
        logger.error(f"Error fetching OHLCV for {ticker}: {e}")
        return pd.DataFrame()


def compute_indicators(df: pd.DataFrame) -> dict:
    """
    Compute technical indicators on a close-price DataFrame.
    Returns a dict of indicator arrays (as lists for JSON serialization).
    """
    def _clean_series(s: pd.Series) -> list:
        return [float(x) if not pd.isna(x) else None for x in s]
    if df.empty or "close" not in df.columns:
        return {}

    close = df["close"]
    volume = df.get("volume", pd.Series(dtype=float))
    indicators = {}

    try:
        # ── Moving Averages ──────────────────────────────────────────────
        sma20 = SMAIndicator(close, window=20).sma_indicator()
        sma50 = SMAIndicator(close, window=50).sma_indicator()
        ema12 = EMAIndicator(close, window=12).ema_indicator()
        ema26 = EMAIndicator(close, window=26).ema_indicator()

        indicators["sma_20"] = _clean_series(sma20)
        indicators["sma_50"] = _clean_series(sma50)
        indicators["ema_12"] = _clean_series(ema12)
        indicators["ema_26"] = _clean_series(ema26)

        # ── RSI ──────────────────────────────────────────────────────────
        rsi = RSIIndicator(close, window=14).rsi()
        indicators["rsi_14"] = _clean_series(rsi)

        # ── MACD ─────────────────────────────────────────────────────────
        macd_obj = MACD(close)
        indicators["macd"] = _clean_series(macd_obj.macd())
        indicators["macd_signal"] = _clean_series(macd_obj.macd_signal())
        indicators["macd_histogram"] = _clean_series(macd_obj.macd_diff())

        # ── Bollinger Bands ──────────────────────────────────────────────
        bb = BollingerBands(close)
        indicators["bb_upper"] = _clean_series(bb.bollinger_hband())
        indicators["bb_middle"] = _clean_series(bb.bollinger_mavg())
        indicators["bb_lower"] = _clean_series(bb.bollinger_lband())
        indicators["bb_pct_b"] = _clean_series(bb.bollinger_pband())

        # ── Volume SMA ───────────────────────────────────────────────────
        if not volume.empty and len(volume) >= 20:
            vol_sma = SMAIndicator(volume.astype(float), window=20).sma_indicator()
            indicators["volume_sma_20"] = _clean_series(vol_sma)

        # ── Latest snapshot (for ML features) ────────────────────────────
        latest_idx = len(close) - 1
        indicators["latest"] = {
            "close": float(close.iloc[latest_idx]),
            "sma_20": float(sma20.iloc[latest_idx]) if not pd.isna(sma20.iloc[latest_idx]) else None,
            "sma_50": float(sma50.iloc[latest_idx]) if not pd.isna(sma50.iloc[latest_idx]) else None,
            "ema_12": float(ema12.iloc[latest_idx]) if not pd.isna(ema12.iloc[latest_idx]) else None,
            "ema_26": float(ema26.iloc[latest_idx]) if not pd.isna(ema26.iloc[latest_idx]) else None,
            "rsi_14": float(rsi.iloc[latest_idx]) if not pd.isna(rsi.iloc[latest_idx]) else None,
            "macd": float(macd_obj.macd().iloc[latest_idx]) if not pd.isna(macd_obj.macd().iloc[latest_idx]) else None,
            "macd_histogram": float(macd_obj.macd_diff().iloc[latest_idx]) if not pd.isna(macd_obj.macd_diff().iloc[latest_idx]) else None,
            "bb_pct_b": float(bb.bollinger_pband().iloc[latest_idx]) if not pd.isna(bb.bollinger_pband().iloc[latest_idx]) else None,
        }

    except Exception as e:
        logger.error(f"Error computing indicators: {e}")

    return indicators


def get_latest_price(ticker: str) -> Optional[float]:
    """Get the most recent closing price for a ticker."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            return None
        val = hist["Close"].iloc[-1]
        return float(val) if not pd.isna(val) else None
    except Exception as e:
        logger.error(f"Error fetching latest price for {ticker}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#   NEWS INGESTION (yfinance primary → Finnhub fallback)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_news(ticker: str, max_headlines: int = 3) -> List[NewsItem]:
    """
    Fetch the latest news headlines for a ticker.
    Primary: yfinance  |  Fallback: Finnhub
    Returns up to `max_headlines` NewsItem objects.
    """
    news_items = _fetch_news_yfinance(ticker, max_headlines)

    if not news_items:
        logger.info(f"yfinance news empty for {ticker}, falling back to Finnhub")
        news_items = _fetch_news_finnhub(ticker, max_headlines)

    if not news_items:
        logger.warning(f"No news found for {ticker} from any source")

    return news_items[:max_headlines]


def _fetch_news_yfinance(ticker: str, max_headlines: int) -> List[NewsItem]:
    """Pull news from yfinance .news attribute."""
    try:
        t = yf.Ticker(ticker)
        raw_news = t.news

        if not raw_news:
            return []

        items = []
        for article in raw_news[:max_headlines]:
            # yfinance news structure may vary — handle robustly
            headline = ""
            source = ""
            url = ""
            published = None

            if isinstance(article, dict):
                headline = article.get("title", article.get("headline", ""))
                source = article.get("publisher", article.get("source", ""))
                url = article.get("link", article.get("url", ""))
                pub_ts = article.get("providerPublishTime", article.get("published", None))
                if pub_ts and isinstance(pub_ts, (int, float)):
                    published = datetime.fromtimestamp(pub_ts, tz=timezone.utc)

            if headline:
                items.append(NewsItem(
                    headline=headline,
                    source=source,
                    url=url,
                    published_at=published,
                ))

        return items

    except Exception as e:
        logger.error(f"yfinance news error for {ticker}: {e}")
        return []


def _fetch_news_finnhub(ticker: str, max_headlines: int) -> List[NewsItem]:
    """
    Pull company news from Finnhub free API.
    Note: Finnhub uses plain ticker symbols (no .NS suffix).
    """
    if not settings.finnhub_api_key:
        logger.warning("Finnhub API key not configured, skipping fallback")
        return []

    try:
        client = _get_finnhub_client()

        # Strip exchange suffix for Finnhub (.NS, .BO)
        clean_ticker = ticker.split(".")[0]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")

        raw_news = client.company_news(clean_ticker, _from=three_days_ago, to=today)

        if not raw_news:
            return []

        items = []
        for article in raw_news[:max_headlines]:
            published = None
            if article.get("datetime"):
                published = datetime.fromtimestamp(article["datetime"], tz=timezone.utc)

            items.append(NewsItem(
                headline=article.get("headline", ""),
                source=article.get("source", ""),
                url=article.get("url", ""),
                published_at=published,
            ))

        return items

    except Exception as e:
        logger.error(f"Finnhub news error for {ticker}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#   BULK SCREENER (STAGE 1)
# ═══════════════════════════════════════════════════════════════════════════════

def bulk_screener(tickers: List[str], max_results: int = 10) -> List[str]:
    """
    Fast pre-screening across multiple tickers using yfinance bulk download.
    Returns top tickers exhibiting volume spikes and RSI momentum.
    """
    logger.info(f"Running bulk pre-screener on {len(tickers)} tickers...")
    try:
        df = yf.download(tickers, period="2mo", interval="1d", group_by="ticker", threads=True, progress=False)
        
        candidates = []
        for ticker in tickers:
            try:
                t_df = df[ticker] if len(tickers) > 1 else df
                if t_df.empty or "Close" not in t_df or "Volume" not in t_df:
                    continue
                
                close = t_df["Close"].dropna()
                volume = t_df["Volume"].dropna()
                if len(close) < 20:
                    continue
                
                rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
                vol_sma = SMAIndicator(volume, window=20).sma_indicator().iloc[-1]
                current_vol = volume.iloc[-1]
                
                if pd.isna(rsi) or pd.isna(vol_sma) or vol_sma == 0:
                    continue

                vol_ratio = current_vol / vol_sma
                
                # Rule: RSI > 50 (momentum) AND Volume spike > 1.2x average
                if rsi > 50 and vol_ratio > 1.2:
                    candidates.append((ticker, rsi, vol_ratio))
            except Exception:
                continue

        # Sort by volume spike magnitude
        candidates.sort(key=lambda x: x[2], reverse=True)
        top_tickers = [c[0] for c in candidates[:max_results]]
        
        logger.info(f"Bulk screener identified top {len(top_tickers)} candidates.")
        
        if not top_tickers:
            # Fallback if no stocks meet the strict criteria today
            logger.info("No stocks met strict criteria. Falling back to top 5 by RSI.")
            candidates.sort(key=lambda x: x[1], reverse=True)
            top_tickers = [c[0] for c in candidates[:5]]
            if not top_tickers:
                top_tickers = tickers[:max_results]
                
        return top_tickers
    except Exception as e:
        logger.error(f"Bulk screener error: {e}")
        return tickers[:max_results]

# ═══════════════════════════════════════════════════════════════════════════════
#   FULL INGESTION PIPELINE

async def ingest_ticker_data(ticker: str) -> dict:
    """
    Full ingestion pipeline for a single ticker:
    1. Fetch OHLCV data
    2. Compute technical indicators
    3. Fetch news headlines
    4. Cache to MongoDB
    Returns a dict with all ingested data.
    """
    logger.info(f"Ingesting data for {ticker}...")

    # 1. Market data
    df = fetch_ohlcv(ticker)
    if df.empty:
        return {"ticker": ticker, "error": "No OHLCV data available"}

    # 2. Indicators
    indicators = compute_indicators(df)

    # 3. News
    news = fetch_news(ticker)

    # 4. Prepare OHLCV bars for storage
    bars = []
    dt_col = "datetime" if "datetime" in df.columns else df.columns[0]
    for _, row in df.iterrows():
        bars.append({
            "timestamp": row[dt_col].isoformat() if hasattr(row[dt_col], "isoformat") else str(row[dt_col]),
            "open": float(row.get("open", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "close": float(row.get("close", 0)),
            "volume": int(row.get("volume", 0)),
        })

    # 5. Cache to MongoDB (graceful if DB unavailable)
    market_doc = {
        "ticker": ticker,
        "bars": bars,
        "indicators": indicators,
        "news": [n.model_dump() for n in news],
        "last_updated": datetime.now(timezone.utc),
    }

    try:
        collection = get_market_data_collection()
        await collection.update_one(
            {"ticker": ticker},
            {"$set": market_doc},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"Could not cache {ticker} to MongoDB: {e}")

    logger.info(
        f"Ingested {ticker}: {len(bars)} bars, "
        f"{len(indicators)} indicator groups, {len(news)} headlines"
    )

    return {
        "ticker": ticker,
        "bars_count": len(bars),
        "latest_price": indicators.get("latest", {}).get("close"),
        "indicators": indicators.get("latest", {}),
        "news": [n.model_dump() for n in news],
    }


async def ingest_all_tickers() -> List[dict]:
    """Run the full ingestion pipeline for all watchlist tickers."""
    results = []
    for ticker in settings.watchlist:
        result = await ingest_ticker_data(ticker)
        results.append(result)
    return results
