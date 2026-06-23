"""
Real-Time WebSocket API — bridges Dhan MarketFeed to the frontend.

Endpoints:
  WS  /api/ws/market   — live ticks, candles, portfolio, alerts
  GET /api/realtime/status — connection status
  GET /api/realtime/prices — snapshot of all live prices
"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from market_feed import (
    register_ws_client,
    unregister_ws_client,
    get_feed_status,
    get_all_live_prices,
    get_candles,
    subscribe_ticker,
    is_feed_connected,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/market")
async def market_websocket(ws: WebSocket):
    """
    Real-time market data WebSocket endpoint.

    Frontend sends:
      {"type": "subscribe", "ticker": "RELIANCE.NS"}
      {"type": "set_interval", "interval": "5m"}
      {"type": "ping"}

    Backend sends:
      {"type": "ticks", "data": [{"ticker": ..., "ltp": ..., ...}, ...]}
      {"type": "candles", "data": [{"ticker": ..., "interval": ..., ...}, ...]}
      {"type": "portfolio", "cash": ..., "total_value": ..., "holdings": [...]}
      {"type": "alert", "ticker": ..., "message": ..., "action": "SELL"}
      {"type": "status", "connected": true, ...}
      {"type": "pong"}
    """
    await ws.accept()
    await register_ws_client(ws)

    # Send initial status
    try:
        await ws.send_text(json.dumps({
            "type": "status",
            **get_feed_status(),
        }))
    except Exception:
        pass

    try:
        while True:
            # Wait for messages from the frontend
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "subscribe":
                    ticker = msg.get("ticker", "")
                    if ticker:
                        subscribe_ticker(ticker)
                        # Send existing candles for this ticker
                        interval = msg.get("interval", "1m")
                        candles = get_candles(ticker, interval, 500)
                        if candles:
                            await ws.send_text(json.dumps({
                                "type": "history",
                                "ticker": ticker,
                                "interval": interval,
                                "candles": candles,
                            }))
                        logger.debug(f"WS client subscribed to {ticker}")

                elif msg_type == "set_interval":
                    # Client changed chart interval — send cached candles
                    ticker = msg.get("ticker", "")
                    interval = msg.get("interval", "1m")
                    if ticker:
                        candles = get_candles(ticker, interval, 500)
                        await ws.send_text(json.dumps({
                            "type": "history",
                            "ticker": ticker,
                            "interval": interval,
                            "candles": candles,
                        }))

                elif msg_type == "ping":
                    await ws.send_text(json.dumps({
                        "type": "pong",
                        "timestamp": time.time(),
                        "feed_connected": is_feed_connected(),
                    }))

                elif msg_type == "get_prices":
                    prices = get_all_live_prices()
                    await ws.send_text(json.dumps({
                        "type": "prices_snapshot",
                        "data": prices,
                        "timestamp": time.time(),
                    }))

            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected normally")
    except Exception as e:
        logger.debug(f"WebSocket error: {e}")
    finally:
        await unregister_ws_client(ws)


@router.get("/realtime/status")
async def realtime_status():
    """Get the real-time feed connection status."""
    return get_feed_status()


@router.get("/realtime/prices")
async def realtime_prices():
    """Get a snapshot of all live prices."""
    prices = get_all_live_prices()
    return {
        "prices": prices,
        "count": len(prices),
        "feed_connected": is_feed_connected(),
        "timestamp": time.time(),
    }
