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
};
