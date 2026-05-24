"""
Data Ingestion Engine — pulls market data via yfinance and news via
yfinance (primary) / Finnhub (fallback).  Computes technical indicators.
Targeted at NSE/BSE Indian stocks.
"""

import asyncio
import yfinance as yf
import finnhub
import pandas as pd
import numpy as np
from ta.trend import SMAIndicator, EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import logging

from config import settings
from models import NewsItem, OHLCVBar
from database import get_market_data_collection
from nifty_stocks import resolve_watchlist

logger = logging.getLogger(__name__)

# ── Finnhub client (lazy init) ───────────────────────────────────────────────
_finnhub_client: finnhub.Client | None = None

# ── Market Regime Cache (one fetch per cycle, shared across all tickers) ──────
_regime_cache: dict | None = None
_regime_cache_time: float = 0.0
_REGIME_CACHE_TTL = 3600  # 60 minutes — regime doesn't change intra-hour

# ── Daily ATR Cache (per-ticker, refreshed hourly) ───────────────────────────
_daily_atr_cache: dict = {}
_daily_atr_cache_time: float = 0.0
_DAILY_ATR_CACHE_TTL = 3600  # 60 minutes


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
    Compute technical indicators on OHLCV DataFrame.
    Returns a dict of indicator arrays (as lists for JSON serialization)
    and a 'latest' snapshot for the ML/Gemini engines.
    """
    def _clean_series(s: pd.Series) -> list:
        return [float(x) if not pd.isna(x) else None for x in s]

    if df.empty or "close" not in df.columns:
        return {}

    close = df["close"]
    high = df.get("high", pd.Series(dtype=float))
    low = df.get("low", pd.Series(dtype=float))
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

        # ── ATR (Average True Range) ─────────────────────────────────────
        atr = None
        if not high.empty and not low.empty and len(high) >= 14:
            atr_obj = AverageTrueRange(high, low, close, window=14)
            atr = atr_obj.average_true_range()
            indicators["atr_14"] = _clean_series(atr)

        # ── Volume SMA ───────────────────────────────────────────────────
        if not volume.empty and len(volume) >= 20:
            vol_sma = SMAIndicator(volume.astype(float), window=20).sma_indicator()
            indicators["volume_sma_20"] = _clean_series(vol_sma)

        # ── Latest snapshot (for ML features + Gemini) ───────────────────
        latest_idx = len(close) - 1

        latest = {
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

        # Add ATR
        if atr is not None and not pd.isna(atr.iloc[latest_idx]):
            latest["atr_14"] = float(atr.iloc[latest_idx])
            latest["atr_pct"] = float(atr.iloc[latest_idx] / close.iloc[latest_idx])

        # Add volume ratio
        if not volume.empty and len(volume) >= 20:
            current_vol = float(volume.iloc[latest_idx])
            avg_vol = float(vol_sma.iloc[latest_idx]) if not pd.isna(vol_sma.iloc[latest_idx]) else 1.0
            latest["volume_ratio"] = round(current_vol / avg_vol, 3) if avg_vol > 0 else 1.0

        # Add EMA cross signal
        if latest["ema_12"] is not None and latest["ema_26"] is not None:
            latest["ema_cross"] = "BULLISH" if latest["ema_12"] > latest["ema_26"] else "BEARISH"

        # Add SMA trend
        if latest["close"] is not None and latest["sma_20"] is not None:
            latest["price_vs_sma20"] = "ABOVE" if latest["close"] > latest["sma_20"] else "BELOW"
        if latest["close"] is not None and latest["sma_50"] is not None:
            latest["price_vs_sma50"] = "ABOVE" if latest["close"] > latest["sma_50"] else "BELOW"

        indicators["latest"] = latest

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


def get_batch_prices(tickers: List[str]) -> dict:
    """
    Get latest prices for multiple tickers in one batch call.
    Returns {ticker: price} dict.
    """
    if not tickers:
        return {}

    prices = {}
    try:
        df = yf.download(
            tickers,
            period="5d",
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
        )
        for ticker in tickers:
            try:
                if len(tickers) > 1:
                    close = df[ticker]["Close"].dropna()
                else:
                    close = df["Close"].dropna()
                if not close.empty:
                    prices[ticker] = float(close.iloc[-1])
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Batch price fetch error: {e}")

    # Fill missing with individual calls
    for ticker in tickers:
        if ticker not in prices:
            price = get_latest_price(ticker)
            if price:
                prices[ticker] = price

    return prices


# ═══════════════════════════════════════════════════════════════════════════════
#   MARKET REGIME DETECTION (NIFTY 50 bull/bear filter)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_market_regime() -> dict:
    """
    Determine if the broad market is in a BULLISH or BEARISH regime.

    Logic: If NIFTY 50 close > 50-day SMA → BULLISH, else BEARISH.
    Cached for 60 minutes (regime doesn't flip intra-hour).

    Returns:
        {
            "regime": "BULLISH" | "BEARISH",
            "nifty_close": float,
            "nifty_sma50": float,
            "gap_pct": float,  # how far above/below SMA (positive = bullish)
        }
    """
    import time as _t
    global _regime_cache, _regime_cache_time

    # Return cached if fresh
    if _regime_cache and (_t.monotonic() - _regime_cache_time) < _REGIME_CACHE_TTL:
        return _regime_cache

    try:
        index_ticker = settings.market_regime_index  # ^NSEI
        sma_period = settings.market_regime_sma_period  # 50

        df = yf.download(
            index_ticker,
            period="90d",
            interval="1d",
            progress=False,
        )

        if df.empty or len(df) < sma_period:
            logger.warning(
                f"Insufficient NIFTY data ({len(df)} bars, need {sma_period}). "
                f"Defaulting to BULLISH regime."
            )
            result = {
                "regime": "BULLISH",
                "nifty_close": 0.0,
                "nifty_sma50": 0.0,
                "gap_pct": 0.0,
            }
            _regime_cache = result
            _regime_cache_time = _t.monotonic()
            return result

        # Handle MultiIndex columns from yfinance
        close_col = df["Close"]
        if hasattr(close_col, "columns"):
            close_col = close_col.iloc[:, 0]

        nifty_close = float(close_col.iloc[-1])
        sma50 = float(close_col.rolling(window=sma_period).mean().iloc[-1])

        if pd.isna(sma50) or sma50 <= 0:
            regime = "BULLISH"
            gap_pct = 0.0
        else:
            regime = "BULLISH" if nifty_close > sma50 else "BEARISH"
            gap_pct = round((nifty_close - sma50) / sma50 * 100, 2)

        result = {
            "regime": regime,
            "nifty_close": round(nifty_close, 2),
            "nifty_sma50": round(sma50, 2),
            "gap_pct": gap_pct,
        }

        _regime_cache = result
        _regime_cache_time = _t.monotonic()

        logger.info(
            f"MARKET REGIME: {regime} | NIFTY={nifty_close:,.2f} vs "
            f"SMA50={sma50:,.2f} (gap {gap_pct:+.2f}%)"
        )
        return result

    except Exception as e:
        logger.warning(f"Market regime fetch failed: {e}. Defaulting to BULLISH.")
        result = {
            "regime": "BULLISH",
            "nifty_close": 0.0,
            "nifty_sma50": 0.0,
            "gap_pct": 0.0,
        }
        _regime_cache = result
        import time as _t2
        _regime_cache_time = _t2.monotonic()
        return result


def fetch_daily_atr(ticker: str, period: int = 14) -> Optional[float]:
    """
    Fetch daily ATR for position sizing (more meaningful than hourly ATR).

    Professional funds size positions using daily ATR because it captures
    the true daily volatility range, not intraday noise.

    Returns the 14-day daily ATR value, or None if unavailable.
    """
    import time as _t
    global _daily_atr_cache, _daily_atr_cache_time

    # Reset cache if stale
    if (_t.monotonic() - _daily_atr_cache_time) > _DAILY_ATR_CACHE_TTL:
        _daily_atr_cache = {}
        _daily_atr_cache_time = _t.monotonic()

    # Return cached if available
    if ticker in _daily_atr_cache:
        return _daily_atr_cache[ticker]

    try:
        df = yf.download(
            ticker,
            period="60d",
            interval="1d",
            progress=False,
        )

        if df.empty or len(df) < period + 1:
            return None

        # Handle MultiIndex columns
        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        if hasattr(high, "columns"):
            high = high.iloc[:, 0]
        if hasattr(low, "columns"):
            low = low.iloc[:, 0]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]

        atr_obj = AverageTrueRange(high, low, close, window=period)
        atr_series = atr_obj.average_true_range()
        atr_val = float(atr_series.iloc[-1])

        if pd.isna(atr_val) or atr_val <= 0:
            return None

        _daily_atr_cache[ticker] = round(atr_val, 2)
        logger.debug(f"Daily ATR for {ticker}: Rs.{atr_val:.2f}")
        return round(atr_val, 2)

    except Exception as e:
        logger.debug(f"Could not fetch daily ATR for {ticker}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#   NEWS INGESTION (yfinance primary → Finnhub fallback)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_news(ticker: str, max_headlines: int = 5) -> List[NewsItem]:
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


import time as _time  # for bulk screener delays


def _chunked_yf_download(
    tickers: List[str],
    chunk_size: int = 50,
    max_retries: int = 3,
    **yf_kwargs,
) -> pd.DataFrame:
    """
    Download yfinance data in chunks with adaptive backoff.

    Better than fixed delays between every chunk because:
    - Only sleeps 1s between normal chunks (minimal overhead)
    - If rate-limited, retries that specific chunk with exponential backoff
    - Failed tickers in one chunk don't block the rest

    Args:
        tickers: Full list of tickers to download.
        chunk_size: Number of tickers per batch (50 is safe for Yahoo).
        max_retries: Max retries per chunk on rate limit errors.
        **yf_kwargs: Passed through to yf.download (period, interval, etc.)
    """
    chunks = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]
    all_frames = []

    for chunk_idx, chunk in enumerate(chunks):
        for attempt in range(max_retries):
            try:
                df = yf.download(
                    chunk,
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    **yf_kwargs,
                )
                if not df.empty:
                    all_frames.append((chunk, df))
                logger.debug(
                    f"Bulk download chunk {chunk_idx + 1}/{len(chunks)} "
                    f"({len(chunk)} tickers) OK"
                )
                break  # Success — move to next chunk
            except Exception as e:
                err_str = str(e)
                if "Too Many Requests" in err_str or "Rate" in err_str or "429" in err_str:
                    wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.warning(
                        f"Rate limited on chunk {chunk_idx + 1} "
                        f"(attempt {attempt + 1}/{max_retries}), waiting {wait}s..."
                    )
                    _time.sleep(wait)
                else:
                    logger.warning(f"Chunk {chunk_idx + 1} download error: {err_str[:100]}")
                    break  # Non-rate-limit error, skip this chunk

        # Brief pause between chunks to avoid triggering rate limits
        if chunk_idx < len(chunks) - 1:
            _time.sleep(1.0)

    # Merge all chunk DataFrames
    if not all_frames:
        return pd.DataFrame()

    if len(all_frames) == 1:
        return all_frames[0][1]

    # For multi-ticker downloads, yfinance returns MultiIndex columns
    # We need to merge them carefully
    merged = pd.concat([df for _, df in all_frames], axis=1)
    return merged


def bulk_screener(tickers: List[str], max_results: int = 10) -> List[str]:
    """
    Fast pre-screening across multiple tickers using chunked yfinance download.
    Returns top tickers exhibiting volume spikes and RSI momentum.

    Downloads in batches of 50 to avoid Yahoo Finance rate limits.
    """
    logger.info(f"Running bulk pre-screener on {len(tickers)} tickers (chunked)...")
    try:
        df = _chunked_yf_download(
            tickers,
            chunk_size=50,
            period="60d",
            interval="1h",
        )

        if df.empty:
            logger.warning("Bulk screener got empty DataFrame, using fallback tickers")
            return tickers[:max_results]

        candidates = []
        ranked = []
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

                ranked.append((ticker, rsi, vol_ratio))

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
            logger.info("No stocks met strict criteria. Falling back to top by RSI.")
            ranked.sort(key=lambda x: x[1], reverse=True)
            top_tickers = [c[0] for c in ranked[:max_results]]
            if not top_tickers:
                top_tickers = tickers[:max_results]

        return top_tickers
    except Exception as e:
        logger.error(f"Bulk screener error: {e}")
        return tickers[:max_results]


# ═══════════════════════════════════════════════════════════════════════════════
#   FULL INGESTION PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

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

    try:
        # Run synchronous blocking I/O (yfinance, finnhub) in a thread pool
        # so asyncio.wait_for(timeout=120) can actually cancel it
        market_doc = await asyncio.to_thread(build_market_data_doc, ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}

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
        f"Ingested {ticker}: {len(market_doc['bars'])} bars, "
        f"{len(market_doc['indicators'])} indicator groups, {len(market_doc['news'])} headlines"
    )

    return {
        "ticker": ticker,
        "bars_count": len(market_doc["bars"]),
        "latest_price": market_doc["indicators"].get("latest", {}).get("close"),
        "indicators": market_doc["indicators"].get("latest", {}),
        "news": market_doc["news"],
    }


def build_market_data_doc(ticker: str) -> dict:
    """
    Build a full market_data-shaped document from live providers.
    This is used both for Mongo caching and direct chart responses if Mongo is offline.
    """
    df = fetch_ohlcv(ticker)
    if df.empty:
        raise ValueError("No OHLCV data available")

    indicators = compute_indicators(df)
    news = fetch_news(ticker)
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

    return {
        "ticker": ticker,
        "bars": bars,
        "indicators": indicators,
        "news": [n.model_dump() for n in news],
        "last_updated": datetime.now(timezone.utc),
    }


async def ingest_all_tickers() -> List[dict]:
    """Run the full ingestion pipeline for all watchlist tickers."""
    results = []
    for ticker in resolve_watchlist(settings.watchlist):
        result = await ingest_ticker_data(ticker)
        results.append(result)
    return results
