import { useCallback, useEffect, useRef, useState } from "react";
import { getWsUrl } from "../api";

/**
 * useRealtimeFeed — React hook for the real-time market WebSocket.
 *
 * Provides:
 *   - Live price ticks (batched every 250ms from backend)
 *   - Live candle updates for chart
 *   - Portfolio value updates
 *   - Trade alerts (stop-loss, trailing stop triggers)
 *   - Connection status
 *
 * Batches React state updates to prevent re-render thrashing.
 */
export default function useRealtimeFeed() {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const reconnectAttempts = useRef(0);
  const pingInterval = useRef(null);

  const [isConnected, setIsConnected] = useState(false);
  const [feedConnected, setFeedConnected] = useState(false);

  // Price cache: { "RELIANCE.NS": { ltp, change, change_pct, volume, ... } }
  const [prices, setPrices] = useState({});

  // Latest candle updates: [{ ticker, interval, time, open, high, low, close, volume }]
  const [candles, setCandles] = useState([]);

  // Portfolio live update
  const [livePortfolio, setLivePortfolio] = useState(null);

  // Alert queue
  const [alerts, setAlerts] = useState([]);

  // Candle history (when switching ticker/interval)
  const [candleHistory, setCandleHistory] = useState(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(getWsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        reconnectAttempts.current = 0;

        // Start ping/pong keepalive
        pingInterval.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 15000);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);

          switch (msg.type) {
            case "ticks":
              // Batch update price cache
              setPrices((prev) => {
                const next = { ...prev };
                for (const tick of msg.data || []) {
                  next[tick.ticker] = tick;
                }
                return next;
              });
              break;

            case "candles":
              setCandles(msg.data || []);
              break;

            case "portfolio":
              setLivePortfolio(msg);
              break;

            case "alert":
              setAlerts((prev) => [msg, ...prev].slice(0, 50));
              break;

            case "status":
              setFeedConnected(msg.connected || false);
              break;

            case "pong":
              setFeedConnected(msg.feed_connected || false);
              break;

            case "history":
              setCandleHistory(msg);
              break;

            case "prices_snapshot":
              setPrices((prev) => {
                const next = { ...prev };
                for (const [ticker, ltp] of Object.entries(msg.data || {})) {
                  next[ticker] = { ticker, ltp, change: 0, change_pct: 0 };
                }
                return next;
              });
              break;
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        clearInterval(pingInterval.current);

        // Exponential backoff reconnect
        const delay = Math.min(1000 * 2 ** reconnectAttempts.current, 30000);
        reconnectAttempts.current += 1;
        reconnectTimer.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      // WebSocket not available
    }
  }, []);

  const disconnect = useCallback(() => {
    clearTimeout(reconnectTimer.current);
    clearInterval(pingInterval.current);
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
  }, []);

  const subscribe = useCallback((ticker, interval = "1m") => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN && ticker) {
      ws.send(JSON.stringify({ type: "subscribe", ticker, interval }));
    }
  }, []);

  const setChartInterval = useCallback((ticker, interval) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN && ticker) {
      ws.send(JSON.stringify({ type: "set_interval", ticker, interval }));
    }
  }, []);

  const requestPrices = useCallback(() => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "get_prices" }));
    }
  }, []);

  const clearAlerts = useCallback(() => setAlerts([]), []);

  useEffect(() => {
    connect();
    return disconnect;
  }, [connect, disconnect]);

  return {
    isConnected,
    feedConnected,
    prices,
    candles,
    livePortfolio,
    alerts,
    candleHistory,
    subscribe,
    setChartInterval,
    requestPrices,
    clearAlerts,
  };
}
