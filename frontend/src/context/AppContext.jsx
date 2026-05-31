import { createContext, useCallback, useContext, useEffect, useMemo, useReducer } from "react";
import { api, apiBase } from "../api";
import { normalizeTicker } from "../format";

const emptyDashboard = {
  portfolio: null,
  portfolioHistory: [],
  analyses: [],
  trades: [],
  watchlist: [],
  watchlistInfo: null,
  news: null,
};

const emptyPnl = {
  daily_pnl: { value: 0, pct: 0 },
  weekly_pnl: { value: 0, pct: 0 },
  yearly_pnl: { value: 0, pct: 0 },
  total_realized_pnl: 0,
  total_unrealized_pnl: 0,
  total_portfolio_value: 0,
  cash: 0,
  total_pnl: 0,
  total_pnl_pct: 0,
};

const initialState = {
  dashboard: emptyDashboard,
  pnlData: emptyPnl,
  selectedTicker: "",
  marketData: null,
  analysisHistory: [],
  tradeHistory: { trades: [], total_count: 0, page: 1, total_pages: 1 },
  tradeHistoryPage: 1,
  capitalAmount: "1000000",
  status: { loading: true, action: "", online: false, error: "", message: "" },
};

function reducer(state, action) {
  switch (action.type) {
    case "SET_DASHBOARD":
      return { ...state, dashboard: action.payload };
    case "SET_PNL":
      return { ...state, pnlData: action.payload || emptyPnl };
    case "SET_TICKER":
      return { ...state, selectedTicker: action.payload };
    case "SET_MARKET_DATA":
      return { ...state, marketData: action.payload };
    case "SET_ANALYSIS_HISTORY":
      return { ...state, analysisHistory: action.payload };
    case "SET_TRADE_HISTORY":
      return { ...state, tradeHistory: action.payload };
    case "SET_TRADE_PAGE":
      return { ...state, tradeHistoryPage: action.payload };
    case "SET_CAPITAL":
      return { ...state, capitalAmount: action.payload };
    case "SET_STATUS":
      return { ...state, status: { ...state.status, ...action.payload } };
    case "RESET":
      return { ...initialState, status: { ...initialState.status, loading: false } };
    default:
      return state;
  }
}

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  const loadTicker = useCallback(async (ticker) => {
    if (!ticker) return;
    dispatch({ type: "SET_TICKER", payload: ticker });
    dispatch({ type: "SET_STATUS", payload: { action: "Loading ticker", error: "" } });
    try {
      const [market, history] = await Promise.all([
        api.marketData(ticker),
        api.analysisHistory(ticker, 80),
      ]);
      dispatch({ type: "SET_MARKET_DATA", payload: market || null });
      dispatch({ type: "SET_ANALYSIS_HISTORY", payload: history?.analyses || [] });
    } catch (error) {
      dispatch({ type: "SET_MARKET_DATA", payload: null });
      dispatch({ type: "SET_ANALYSIS_HISTORY", payload: [] });
      dispatch({ type: "SET_STATUS", payload: { error: `Could not load ${ticker}. ${error.message}` } });
    } finally {
      dispatch({ type: "SET_STATUS", payload: { action: "" } });
    }
  }, []);

  const loadDashboard = useCallback(async ({ quiet = false, keepTicker = "" } = {}) => {
    dispatch({
      type: "SET_STATUS",
      payload: { loading: !quiet, action: quiet ? "Refreshing" : "Connecting", error: "", message: "" },
    });
    try {
      const [health, watchlistInfo, portfolio, portfolioHistory, analyses, trades, news, pnl] =
        await Promise.all([
          api.health(),
          api.watchlist(),
          api.portfolio(),
          api.portfolioHistory(120),
          api.latestAnalyses(),
          api.trades(150),
          api.latestNews().catch(() => null),
          api.pnlAnalytics().catch(() => emptyPnl),
        ]);

      dispatch({ type: "SET_PNL", payload: pnl || emptyPnl });

      const nextDashboard = {
        portfolio,
        portfolioHistory: portfolioHistory?.snapshots || [],
        analyses: analyses?.analyses || [],
        trades: trades?.trades || [],
        watchlist: watchlistInfo?.watchlist || [],
        watchlistInfo,
        news,
      };
      dispatch({ type: "SET_DASHBOARD", payload: nextDashboard });
      dispatch({ type: "SET_CAPITAL", payload: String(nextDashboard.portfolio?.initial_balance || 1000000) });

      const nextTicker =
        keepTicker ||
        nextDashboard.analyses[0]?.ticker ||
        nextDashboard.portfolio?.holdings?.[0]?.ticker ||
        nextDashboard.watchlist[0] ||
        "";

      dispatch({
        type: "SET_STATUS",
        payload: {
          loading: false,
          action: "",
          online: Boolean(health),
          error: "",
          message: quiet ? "Dashboard refreshed." : "",
        },
      });

      if (nextTicker) {
        await loadTicker(nextTicker);
      }
    } catch (error) {
      dispatch({ type: "SET_DASHBOARD", payload: emptyDashboard });
      dispatch({ type: "SET_MARKET_DATA", payload: null });
      dispatch({ type: "SET_ANALYSIS_HISTORY", payload: [] });
      dispatch({
        type: "SET_STATUS",
        payload: {
          loading: false,
          action: "",
          online: false,
          error: `Backend unavailable at ${apiBase}. ${error.message}`,
          message: "",
        },
      });
    }
  }, [loadTicker]);

  const runAnalysis = useCallback(async () => {
    dispatch({ type: "SET_STATUS", payload: { action: "Running analysis", message: "", error: "" } });
    try {
      const result = await api.triggerAnalysis();
      dispatch({
        type: "SET_STATUS",
        payload: { message: `Analysis completed: ${(result.results || []).length} tickers processed.` },
      });
      await loadDashboard({ quiet: true, keepTicker: state.selectedTicker });
    } catch (error) {
      dispatch({ type: "SET_STATUS", payload: { error: `Analysis trigger failed. ${error.message}` } });
    } finally {
      dispatch({ type: "SET_STATUS", payload: { action: "" } });
    }
  }, [loadDashboard, state.selectedTicker]);

  const refreshMarket = useCallback(async () => {
    dispatch({ type: "SET_STATUS", payload: { action: "Refreshing market", message: "", error: "" } });
    try {
      const result = await api.refreshMarket();
      dispatch({
        type: "SET_STATUS",
        payload: { message: `Market cache refreshed: ${Number(result.tickers_updated || 0)} tickers.` },
      });
      await loadDashboard({ quiet: true, keepTicker: state.selectedTicker });
    } catch (error) {
      dispatch({ type: "SET_STATUS", payload: { error: `Market refresh failed. ${error.message}` } });
    } finally {
      dispatch({ type: "SET_STATUS", payload: { action: "" } });
    }
  }, [loadDashboard, state.selectedTicker]);

  const resetPaperAccount = useCallback(async () => {
    const amount = Number(state.capitalAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      dispatch({ type: "SET_STATUS", payload: { error: "Enter a valid starting capital amount." } });
      return;
    }
    dispatch({ type: "SET_STATUS", payload: { action: "Resetting portfolio", message: "", error: "" } });
    try {
      await api.resetPortfolio(amount, true);
      dispatch({
        type: "SET_STATUS",
        payload: { message: `Portfolio reset to ₹${amount.toLocaleString("en-IN")}.` },
      });
      await loadDashboard({ quiet: true });
    } catch (error) {
      dispatch({ type: "SET_STATUS", payload: { error: `Portfolio reset failed. ${error.message}` } });
    } finally {
      dispatch({ type: "SET_STATUS", payload: { action: "" } });
    }
  }, [loadDashboard, state.capitalAmount]);

  const loadTradeHistory = useCallback(async (page = 1, pageSize = 30) => {
    try {
      const data = await api.tradeHistory(page, pageSize);
      dispatch({ type: "SET_TRADE_HISTORY", payload: data });
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  const selectedAnalysis = useMemo(() => {
    if (state.analysisHistory.length) return state.analysisHistory[0];
    return (
      state.dashboard.analyses.find(
        (item) => normalizeTicker(item.ticker) === normalizeTicker(state.selectedTicker)
      ) || null
    );
  }, [state.analysisHistory, state.dashboard.analyses, state.selectedTicker]);

  const selectedTrades = useMemo(
    () =>
      state.dashboard.trades.filter(
        (trade) => normalizeTicker(trade.ticker) === normalizeTicker(state.selectedTicker)
      ),
    [state.dashboard.trades, state.selectedTicker]
  );

  const watchlistRows = useMemo(() => {
    const rows = state.dashboard.analyses.map((a) => ({
      ticker: a.ticker,
      price: Number(a.current_price || a.price || 0),
      ml: Number(a.ml_confidence || 0),
      sentiment: Number(a.gemini_sentiment_score || 0),
      action: a.action || "HOLD",
    }));
    const known = new Set(rows.map((r) => normalizeTicker(r.ticker)));
    const fillers = state.dashboard.watchlist
      .filter((t) => !known.has(normalizeTicker(t)))
      .map((t) => ({ ticker: t, price: 0, ml: 0, sentiment: 0, action: "HOLD" }));
    return [...rows, ...fillers].sort((a, b) => b.ml - a.ml || a.ticker.localeCompare(b.ticker));
  }, [state.dashboard.watchlist, state.dashboard.analyses]);

  const value = {
    ...state,
    selectedAnalysis,
    selectedTrades,
    watchlistRows,
    dispatch,
    loadDashboard,
    loadTicker,
    runAnalysis,
    refreshMarket,
    resetPaperAccount,
    loadTradeHistory,
    isBusy: state.status.loading || Boolean(state.status.action),
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) throw new Error("useApp must be used within AppProvider");
  return context;
}
