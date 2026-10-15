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
  // ── V2 Core Endpoints (Strict Telemetry Envelope) ──────────────────────────
  health: () => request("/api/v2/health"),
  portfolio: (mode = null) => request(`/api/v2/portfolio${mode ? `?mode=${mode}` : ""}`),
  portfolioHistory: (limit = 100, mode = null) => {
    const params = new URLSearchParams({ limit });
    if (mode) params.append("mode", mode);
    return request(`/api/v2/portfolio/history?${params.toString()}`);
  },
  latestAnalyses: () => request("/api/v2/analysis/latest"),
  trades: (limit = 50, offset = 0, mode = null) => {
    const params = new URLSearchParams({ limit, offset });
    if (mode) params.append("mode", mode);
    return request(`/api/v2/trades?${params.toString()}`);
  },
  news: () => request("/api/v2/news"),
  pnl: (mode = null) => request(`/api/v2/analytics/pnl${mode ? `?mode=${mode}` : ""}`),
  metaSummary: () => request("/api/v2/meta/summary"),
  tradingStatus: () => request("/api/v2/trading/status"),

  // ── Research Endpoints ─────────────────────────────────────────────────────
  researchStatus: (runId = null) =>
    request(`/api/research/status${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`),
  triggerResearch: (count = null) =>
    request(`/api/research/trigger${count ? `?count=${count}` : ""}`, { method: "POST" }),
  revalidateHof: () => request("/api/research/hof/revalidate", { method: "POST" }),

  // ── Controls & Mode ────────────────────────────────────────────────────────
  getTradingMode: () => request("/api/trading/mode"),
  setTradingMode: (mode) =>
    request("/api/trading/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  getKillSwitch: () => request("/api/trading/kill-switch"),
  toggleKillSwitch: (enabled) =>
    request("/api/trading/kill-switch", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  getDhanStatus: () => request("/api/trading/dhan/status"),
  getDhanCredentials: () => request("/api/trading/dhan/credentials"),
  saveDhanCredentials: (client_id, pin, totp_secret, access_token) =>
    request("/api/trading/dhan/credentials", {
      method: "POST",
      body: JSON.stringify({ client_id, pin, totp_secret, access_token }),
    }),
  getLLMUsage: () => request("/api/llm-config/usage"),

  // ── Legacy Trigger Fallback ────────────────────────────────────────────────
  triggerAnalysis: () => request("/api/trigger-analysis", { method: "POST" }),
};
