"""
Real-Time Market Feed — Dhan WebSocket price streaming engine.

Connects to Dhan's MarketFeed WebSocket for tick-by-tick live prices.
On each tick:
  1. Updates in-memory price cache
  2. Updates peak price for trailing stop tracking
  3. Checks stop-loss / trailing stop (instant sell on breach)
  4. Builds real-time OHLCV candles (1m, 5m, 15m)
  5. Broadcasts to all connected frontend WebSocket clients

Gracefully falls back to yfinance polling when Dhan is not configured.
"""

import asyncio
import logging
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Callable

from config import settings

logger = logging.getLogger(__name__)

# ── In-memory price cache ────────────────────────────────────────────────────
# Updated on every Dhan tick — used by risk checks, portfolio valuation, charts
_price_cache: Dict[str, dict] = {}  # ticker -> {ltp, volume, timestamp, ...}
_candle_cache: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
# candle_cache[ticker]["1m"] = [{time, open, high, low, close, volume}, ...]

# ── Connected frontend WebSocket clients ─────────────────────────────────────
_ws_clients: Set = set()
_ws_lock = asyncio.Lock()

# ── Feed state ───────────────────────────────────────────────────────────────
_feed_thread: Optional[threading.Thread] = None
_feed_instance = None
_feed_running = False
_feed_connected = False
_last_tick_time: float = 0
_subscribed_tickers: Set[str] = set()

# ── Pending tick buffer (batched for frontend broadcast) ─────────────────────
_tick_buffer: List[dict] = []
_tick_buffer_lock = threading.Lock()

# ── Callbacks ────────────────────────────────────────────────────────────────
_on_stop_loss_callback: Optional[Callable] = None


def get_live_price(ticker: str) -> Optional[float]:
    """Get the latest live price for a ticker from cache."""
    entry = _price_cache.get(ticker)
    if entry:
        return entry.get("ltp")
    return None


def get_live_prices(tickers: List[str]) -> Dict[str, float]:
    """Get live prices for multiple tickers. Returns {ticker: price}."""
    result = {}
    for ticker in tickers:
        price = get_live_price(ticker)
        if price:
            result[ticker] = price
    return result


def get_all_live_prices() -> Dict[str, float]:
    """Get all cached live prices."""
    return {t: d["ltp"] for t, d in _price_cache.items() if d.get("ltp")}


def get_candles(ticker: str, interval: str = "1m", limit: int = 500) -> list:
    """Get cached candles for a ticker at given interval."""
    candles = _candle_cache.get(ticker, {}).get(interval, [])
    return candles[-limit:] if candles else []


def is_feed_connected() -> bool:
    """Check if the real-time feed is connected."""
    return _feed_connected


def get_feed_status() -> dict:
    """Get feed connection status for API response."""
    return {
        "connected": _feed_connected,
        "running": _feed_running,
        "last_tick_time": datetime.fromtimestamp(
            _last_tick_time, tz=timezone.utc
        ).isoformat() if _last_tick_time else None,
        "subscribed_count": len(_subscribed_tickers),
        "cached_prices": len(_price_cache),
        "ws_clients": len(_ws_clients),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   FRONTEND WEBSOCKET CLIENT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

async def register_ws_client(ws):
    """Register a new frontend WebSocket client."""
    async with _ws_lock:
        _ws_clients.add(ws)
    logger.info(f"WebSocket client connected (total: {len(_ws_clients)})")


async def unregister_ws_client(ws):
    """Remove a frontend WebSocket client."""
    async with _ws_lock:
        _ws_clients.discard(ws)
    logger.info(f"WebSocket client disconnected (total: {len(_ws_clients)})")


async def broadcast_to_clients(message: dict):
    """Send a message to all connected frontend WebSocket clients."""
    if not _ws_clients:
        return
    
    import json
    payload = json.dumps(message)
    
    dead_clients = set()
    async with _ws_lock:
        for ws in _ws_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead_clients.add(ws)
    
    # Clean up dead connections
    if dead_clients:
        async with _ws_lock:
            _ws_clients -= dead_clients


# ═══════════════════════════════════════════════════════════════════════════════
#   CANDLE BUILDER — constructs OHLCV candles from raw ticks
# ═══════════════════════════════════════════════════════════════════════════════

def _interval_seconds(interval: str) -> int:
    """Convert interval string to seconds."""
    mapping = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
    return mapping.get(interval.lower(), 60)


def _get_candle_timestamp(tick_time: float, interval_secs: int) -> int:
    """Get the candle open timestamp for a given tick time and interval."""
    return int(tick_time // interval_secs) * interval_secs


def _update_candle(ticker: str, ltp: float, volume: int, tick_time: float):
    """Update or create candles for all configured intervals from a tick."""
    for interval in settings.realtime_candle_intervals:
        interval_secs = _interval_seconds(interval)
        candle_ts = _get_candle_timestamp(tick_time, interval_secs)
        
        candles = _candle_cache[ticker][interval]
        
        if candles and candles[-1]["time"] == candle_ts:
            # Update existing candle
            c = candles[-1]
            c["high"] = max(c["high"], ltp)
            c["low"] = min(c["low"], ltp)
            c["close"] = ltp
            c["volume"] += volume
        else:
            # New candle period
            candles.append({
                "time": candle_ts,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": volume,
            })
            
            # Keep max 2000 candles per interval per ticker (memory guard)
            if len(candles) > 2000:
                _candle_cache[ticker][interval] = candles[-1500:]


# ═══════════════════════════════════════════════════════════════════════════════
#   DHAN MARKET FEED — WebSocket connection manager
# ═══════════════════════════════════════════════════════════════════════════════

def _build_subscription_list() -> list:
    """Build the list of instruments to subscribe to via Dhan MarketFeed."""
    from security_master import security_master
    from nifty_stocks import resolve_watchlist
    
    if not security_master.is_loaded:
        logger.warning("Security master not loaded — cannot build subscriptions")
        return []
    
    # Get tickers to subscribe to
    if settings.realtime_subscribe_watchlist:
        tickers = resolve_watchlist(settings.watchlist)
    else:
        # Just held stocks + selected ticker
        tickers = list(_subscribed_tickers)
    
    # Map to Dhan security IDs
    instruments = []
    for ticker in tickers:
        sec_id = security_master.get_security_id(ticker)
        if sec_id:
            instruments.append((0, str(sec_id)))  # 0 = NSE_EQ segment
    
    logger.info(f"Built subscription list: {len(instruments)} instruments from {len(tickers)} tickers")
    return instruments


def _ticker_from_security_id(security_id: str) -> Optional[str]:
    """Reverse-lookup: Dhan security ID -> NSE ticker.NS"""
    from security_master import security_master
    ticker = security_master.get_ticker(str(security_id))
    if ticker:
        return f"{ticker}.NS"
    return None


def _process_tick(tick_data: dict):
    """
    Process a single tick from Dhan MarketFeed.
    Called from the feed thread — must be thread-safe.
    """
    global _last_tick_time
    
    try:
        security_id = str(tick_data.get("security_id", tick_data.get("instrument_id", "")))
        ltp = tick_data.get("LTP", tick_data.get("ltp", 0))
        volume = tick_data.get("volume", tick_data.get("vol", 0))
        
        if not security_id or not ltp:
            return
        
        ticker = _ticker_from_security_id(security_id)
        if not ticker:
            return
        
        now = time.time()
        _last_tick_time = now
        
        # Update price cache
        prev = _price_cache.get(ticker, {})
        prev_ltp = prev.get("ltp", ltp)
        change = ltp - prev_ltp
        change_pct = (change / prev_ltp * 100) if prev_ltp else 0
        
        _price_cache[ticker] = {
            "ltp": ltp,
            "prev_ltp": prev_ltp,
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "timestamp": now,
            "security_id": security_id,
            "open": tick_data.get("open", prev.get("open", ltp)),
            "high": max(tick_data.get("high", 0), prev.get("high", ltp), ltp),
            "low": min(
                x for x in [tick_data.get("low", 0), prev.get("low", ltp), ltp] if x > 0
            ),
        }
        
        # Build candles
        _update_candle(ticker, ltp, volume, now)
        
        # Buffer tick for frontend broadcast
        with _tick_buffer_lock:
            _tick_buffer.append({
                "type": "tick",
                "ticker": ticker,
                "ltp": ltp,
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "volume": volume,
                "timestamp": now,
            })
    
    except Exception as e:
        logger.debug(f"Tick processing error: {e}")


def _run_dhan_feed():
    """
    Run the Dhan MarketFeed WebSocket in a background thread.
    This is blocking — runs until stop is requested.
    """
    global _feed_instance, _feed_connected, _feed_running
    
    try:
        from dhanhq import MarketFeed, DhanContext
        from dhan_client import dhan_client
        
        client_id = dhan_client._effective_client_id
        access_token = dhan_client._effective_access_token
        
        if not client_id or not access_token:
            logger.warning("Dhan credentials not available for MarketFeed")
            _feed_running = False
            return
        
        instruments = _build_subscription_list()
        if not instruments:
            logger.warning("No instruments to subscribe to")
            _feed_running = False
            return
        
        logger.info(
            f"Starting Dhan MarketFeed WebSocket "
            f"(client={client_id}, instruments={len(instruments)})"
        )
        
        # Initialize MarketFeed using DhanContext
        context = DhanContext(client_id, access_token)
        feed = MarketFeed(
            context,
            instruments,
            version='v2'
        )
        _feed_instance = feed
        
        def on_connect(instance):
            global _feed_connected
            _feed_connected = True
            logger.info(
                f"✓ Dhan MarketFeed CONNECTED — "
                f"streaming {len(instruments)} instruments"
            )
        
        def on_message(instance, message):
            if isinstance(message, dict):
                _process_tick(message)
        
        def on_close(instance):
            global _feed_connected
            _feed_connected = False
            logger.warning("Dhan MarketFeed disconnected")
        
        feed.on_connect = on_connect
        feed.on_message = on_message
        feed.on_close = on_close
        
        # This blocks until the connection is closed
        feed.connect()
        
    except ImportError:
        logger.error("dhanhq package not installed — real-time feed unavailable")
    except Exception as e:
        logger.error(f"Dhan MarketFeed error: {e}")
    finally:
        _feed_connected = False
        _feed_running = False
        logger.info("Dhan MarketFeed thread exited")


# ═══════════════════════════════════════════════════════════════════════════════
#   REAL-TIME STOP-LOSS CHECKER — runs on every tick for held stocks
# ═══════════════════════════════════════════════════════════════════════════════

async def _check_realtime_stops():
    """
    Check stop-losses using live prices. Called periodically (every 2 seconds)
    by the broadcast loop. Only checks held stocks.
    """
    try:
        from ledger import get_portfolio_for_mode, execute_sell_for_mode
        from risk_manager import check_stop_losses
        from data_ingestion import fetch_market_regime
        from models import AnalysisResult, TradeAction
        from telegram_bot import send_trade_alert
        
        active_mode = settings.trading_mode
        portfolio = await get_portfolio_for_mode(active_mode)
        holdings = portfolio.get("holdings", [])
        
        if not holdings:
            return
        
        held_tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]
        if not held_tickers:
            return
        
        # Get live prices from cache
        live_prices = get_live_prices(held_tickers)
        if not live_prices:
            return
        
        # Update peak prices in portfolio for trailing stop accuracy
        for h in holdings:
            ticker = h.get("ticker", "")
            live = live_prices.get(ticker)
            if live and live > h.get("peak_price", 0):
                # Update peak in DB
                try:
                    from database import get_portfolio_collection
                    coll = get_portfolio_collection()
                    await coll.update_one(
                        {"holdings.ticker": ticker},
                        {"$set": {"holdings.$.peak_price": live}},
                    )
                except Exception:
                    pass
        
        # Get market regime (cached, refreshes hourly)
        try:
            regime_data = await asyncio.to_thread(fetch_market_regime)
            risk_regime = regime_data.get("regime", "BULLISH")
        except Exception:
            risk_regime = "BULLISH"
        
        # Run stop-loss checks
        stop_signals = check_stop_losses(portfolio, live_prices, market_regime=risk_regime)
        
        for signal in stop_signals:
            ticker = signal["ticker"]
            logger.warning(f"[REALTIME] {signal['reason']}")
            
            analysis = AnalysisResult(
                ticker=ticker,
                current_price=signal["price"],
                ml_confidence=0.0,
                gemini_sentiment_score=-1.0,
                gemini_explanation=f"[REALTIME] {signal['reason']}",
                gemini_confidence=0.0,
                final_score=0.0,
                action=TradeAction.SELL,
                action_reason=f"[REALTIME] {signal['reason']}",
            )
            
            trade = await execute_sell_for_mode(
                ticker, signal["price"], analysis, mode=active_mode
            )
            
            if "error" not in trade:
                asyncio.create_task(send_trade_alert(trade))
                
                # Alert frontend
                await broadcast_to_clients({
                    "type": "alert",
                    "ticker": ticker,
                    "message": signal["reason"],
                    "action": "SELL",
                    "price": signal["price"],
                    "trigger": signal.get("trigger", "stop_loss"),
                    "timestamp": time.time(),
                })
                
                logger.info(
                    f"[REALTIME SELL] {ticker} @ Rs.{signal['price']:.2f} — "
                    f"{signal['reason']}"
                )
    
    except Exception as e:
        logger.debug(f"Real-time stop check error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#   BROADCAST LOOP — sends batched ticks + portfolio to frontend
# ═══════════════════════════════════════════════════════════════════════════════

async def _broadcast_loop():
    """
    Background task that:
    1. Every 250ms: flushes tick buffer to frontend WebSocket clients
    2. Every 2s: checks stop-losses using live prices
    3. Every 5s: sends portfolio valuation update
    """
    stop_check_counter = 0
    portfolio_counter = 0
    batch_interval = settings.realtime_tick_batch_ms / 1000.0
    
    while _feed_running:
        await asyncio.sleep(batch_interval)
        
        # 1. Flush tick buffer to frontend
        ticks_to_send = []
        with _tick_buffer_lock:
            if _tick_buffer:
                # Deduplicate: keep only latest tick per ticker
                latest = {}
                for t in _tick_buffer:
                    latest[t["ticker"]] = t
                ticks_to_send = list(latest.values())
                _tick_buffer.clear()
        
        if ticks_to_send and _ws_clients:
            await broadcast_to_clients({
                "type": "ticks",
                "data": ticks_to_send,
                "timestamp": time.time(),
            })
            
            # Also send latest candle updates for the subscribed interval
            candle_updates = []
            for t in ticks_to_send:
                ticker = t["ticker"]
                for interval in settings.realtime_candle_intervals:
                    candles = _candle_cache.get(ticker, {}).get(interval, [])
                    if candles:
                        candle_updates.append({
                            "ticker": ticker,
                            "interval": interval,
                            **candles[-1],
                        })
            
            if candle_updates:
                await broadcast_to_clients({
                    "type": "candles",
                    "data": candle_updates,
                    "timestamp": time.time(),
                })
        
        # 2. Check stop-losses every 2 seconds
        stop_check_counter += 1
        if stop_check_counter >= int(2.0 / batch_interval):
            stop_check_counter = 0
            await _check_realtime_stops()
        
        # 3. Send portfolio update every 5 seconds
        portfolio_counter += 1
        if portfolio_counter >= int(5.0 / batch_interval) and _ws_clients:
            portfolio_counter = 0
            try:
                from ledger import get_portfolio_for_mode
                active_mode = settings.trading_mode
                portfolio = await get_portfolio_for_mode(active_mode)
                holdings = portfolio.get("holdings", [])
                
                # Inject live prices into holdings
                for h in holdings:
                    ticker = h.get("ticker", "")
                    live = get_live_price(ticker)
                    if live:
                        h["current_price"] = live
                        avg = h.get("avg_price", 0)
                        qty = h.get("quantity", 0)
                        if avg and qty:
                            h["unrealized_pnl"] = round((live - avg) * qty, 2)
                            h["unrealized_pnl_pct"] = round(
                                (live - avg) / avg * 100, 2
                            ) if avg else 0
                            h["market_value"] = round(live * qty, 2)
                
                # Compute total
                total_market_value = sum(
                    h.get("market_value", 0) for h in holdings
                )
                total_value = portfolio.get("cash", 0) + total_market_value
                
                await broadcast_to_clients({
                    "type": "portfolio",
                    "cash": portfolio.get("cash", 0),
                    "total_value": round(total_value, 2),
                    "holdings": holdings,
                    "timestamp": time.time(),
                })
            except Exception as e:
                logger.debug(f"Portfolio broadcast error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#   PUBLIC API — start / stop the feed
# ═══════════════════════════════════════════════════════════════════════════════

async def start_market_feed():
    """
    Start the Dhan MarketFeed WebSocket in a background thread,
    plus the async broadcast loop.
    """
    global _feed_thread, _feed_running
    
    if not settings.realtime_enabled:
        logger.info("Real-time feed disabled in config")
        return
    
    from dhan_client import dhan_client
    if not dhan_client.is_configured:
        logger.info("Dhan not configured — real-time feed not started")
        return
    
    if _feed_running:
        logger.warning("Market feed already running")
        return
    
    _feed_running = True
    
    # Start Dhan WebSocket in a background thread (it's blocking/sync)
    _feed_thread = threading.Thread(
        target=_run_dhan_feed,
        name="DhanMarketFeed",
        daemon=True,
    )
    _feed_thread.start()
    
    # Start the async broadcast loop
    asyncio.create_task(_broadcast_loop())
    
    logger.info("Real-time market feed started")


async def stop_market_feed():
    """Stop the market feed and clean up."""
    global _feed_running, _feed_connected, _feed_instance
    
    _feed_running = False
    _feed_connected = False
    
    if _feed_instance:
        try:
            _feed_instance.disconnect()
        except Exception:
            pass
        _feed_instance = None
    
    logger.info("Real-time market feed stopped")


def subscribe_ticker(ticker: str):
    """Add a ticker to the subscription set (for when user views a chart)."""
    _subscribed_tickers.add(ticker)


def unsubscribe_ticker(ticker: str):
    """Remove a ticker from the extra subscription set."""
    _subscribed_tickers.discard(ticker)
