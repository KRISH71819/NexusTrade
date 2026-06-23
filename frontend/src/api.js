const API_BASE = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    const message = await response.text().catch(() => response.statusText);
    throw new Error(message || `${response.status} ${response.statusText}`);
  }

  return response.json();
}

export const apiBase = API_BASE || "same-origin /api proxy";

export const api = {
  health: () => request("/api/health"),
  watchlist: () => request("/api/market/watchlist/info"),
  portfolio: () => request("/api/portfolio"),
  portfolioHistory: (limit = 120) => request(`/api/portfolio/history?limit=${limit}`),
  latestAnalyses: () => request("/api/analysis/latest"),
  analysisHistory: (ticker, limit = 80) =>
    request(`/api/analysis/${encodeURIComponent(ticker)}?limit=${limit}`),
  trades: (limit = 150) => request(`/api/trades?limit=${limit}`),
  marketData: (ticker) => request(`/api/market/${encodeURIComponent(ticker)}`),
  refreshMarket: () => request("/api/market/refresh", { method: "POST" }),
  triggerAnalysis: () => request("/api/trigger-analysis", { method: "POST" }),
  resetPortfolio: (initialBalance, clearLogs = true) =>
    request("/api/portfolio/reset", {
      method: "POST",
      body: JSON.stringify({
        initial_balance: Number(initialBalance),
        clear_logs: clearLogs,
      }),
    }),
  // News Intelligence
  latestNews: () => request("/api/news/latest"),
  tickerNews: (ticker) => request(`/api/news/${encodeURIComponent(ticker)}`),
  // Analytics
  pnlAnalytics: () => request("/api/analytics/pnl"),
  tradeHistory: (page = 1, pageSize = 50) =>
    request(`/api/analytics/trade-history?page=${page}&page_size=${pageSize}`),

  // ── Trading Mode ──────────────────────────────────────────────────────
  getTradingMode: () => request("/api/trading/mode"),
  setTradingMode: (mode) =>
    request("/api/trading/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  // Kill Switch
  getKillSwitch: () => request("/api/trading/kill-switch"),
  toggleKillSwitch: (enabled) =>
    request("/api/trading/kill-switch", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),

  // Dhan Account
  getDhanStatus: () => request("/api/trading/dhan/status"),
  getDhanFunds: () => request("/api/trading/dhan/funds"),
  getDhanHoldings: () => request("/api/trading/dhan/holdings"),
  syncDhanPortfolio: () => request("/api/trading/dhan/sync", { method: "POST" }),

  // Dhan Credentials (SaaS — save/load from DB)
  getDhanCredentials: () => request("/api/trading/dhan/credentials"),
  saveDhanCredentials: (client_id, pin, totp_secret, access_token) =>
    request("/api/trading/dhan/credentials", {
      method: "POST",
      body: JSON.stringify({ client_id, pin, totp_secret, access_token }),
    }),
  deleteDhanCredentials: () =>
    request("/api/trading/dhan/credentials", { method: "DELETE" }),
  
  // Capital Cap
  getCapitalCap: () => request("/api/trading/capital-cap"),
  setCapitalCap: (max_capital) =>
    request("/api/trading/capital-cap", {
      method: "POST",
      body: JSON.stringify({ max_capital: Number(max_capital) }),
    }),

  // ── Real-Time Feed ────────────────────────────────────────────────────
  realtimeStatus: () => request("/api/realtime/status"),
  realtimePrices: () => request("/api/realtime/prices"),
};

/**
 * Build the WebSocket URL for the real-time market feed.
 * Derives ws:// or wss:// from the REST API base URL.
 */
export function getWsUrl() {
  const base = API_BASE || window.location.origin;
  const wsProtocol = base.startsWith("https") ? "wss" : "ws";
  const httpBase = base.replace(/^https?:\/\//, "");
  return `${wsProtocol}://${httpBase}/api/ws/market`;
}
