import { useState, useEffect, useCallback } from "react";
import { api } from "../api";

/**
 * LLM Multi-Agent Configuration Card.
 *
 * Displays the current LLM chain mode (Kimi K3 analyst → Gemma reviewer),
 * allows toggling between single/chain mode, and shows daily usage stats.
 */
export default function LLMConfigCard() {
  const [config, setConfig] = useState(null);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState("");

  const loadData = useCallback(async () => {
    try {
      const [cfg, usg] = await Promise.all([
        api.getLLMConfig().catch(() => null),
        api.getLLMUsage().catch(() => null),
      ]);
      setConfig(cfg);
      setUsage(usg);
    } catch (e) {
      setError("Failed to load LLM config");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 60_000); // refresh every minute
    return () => clearInterval(interval);
  }, [loadData]);

  const toggleMode = async () => {
    if (!config) return;
    setSwitching(true);
    setError("");
    try {
      const newMode = config.effective_mode === "chain" ? "single" : "chain";
      const result = await api.setLLMMode(newMode);
      await loadData();
      if (result.effective_mode !== newMode) {
        setError(result.message || "Mode degraded — check Kimi API key");
      }
    } catch (e) {
      setError(e.message || "Failed to update mode");
    } finally {
      setSwitching(false);
    }
  };

  if (loading) {
    return (
      <div style={styles.card}>
        <div style={styles.cardHeader}>
          <span style={styles.headerIcon}>🤖</span>
          <span style={styles.headerTitle}>Multi-Agent LLM</span>
        </div>
        <div style={styles.loadingBar}>Loading...</div>
      </div>
    );
  }

  if (!config) {
    return (
      <div style={styles.card}>
        <div style={styles.cardHeader}>
          <span style={styles.headerIcon}>🤖</span>
          <span style={styles.headerTitle}>Multi-Agent LLM</span>
        </div>
        <div style={styles.errorText}>Unable to load LLM config</div>
      </div>
    );
  }

  const isChain = config.effective_mode === "chain";
  const reviews = usage?.review_stats || {};

  return (
    <div style={styles.card}>
      {/* Header */}
      <div style={styles.cardHeader}>
        <span style={styles.headerIcon}>🤖</span>
        <span style={styles.headerTitle}>Multi-Agent LLM</span>
        <span style={{
          ...styles.badge,
          background: isChain ? "rgba(16, 185, 129, 0.15)" : "rgba(251, 191, 36, 0.15)",
          color: isChain ? "#10b981" : "#fbbf24",
        }}>
          {isChain ? "Chain Mode" : "Single Mode"}
        </span>
      </div>

      {/* Mode Toggle */}
      <div style={styles.toggleRow}>
        <span style={styles.toggleLabel}>
          {isChain ? "Kimi K3 → Gemma Review" : "Gemma Only"}
        </span>
        <button
          onClick={toggleMode}
          disabled={switching}
          style={{
            ...styles.toggleButton,
            background: isChain
              ? "linear-gradient(135deg, #10b981, #059669)"
              : "linear-gradient(135deg, #6b7280, #4b5563)",
          }}
        >
          {switching ? "..." : isChain ? "Chain ⚡" : "Single"}
        </button>
      </div>

      {error && <div style={styles.errorText}>{error}</div>}

      {/* Agent Cards */}
      <div style={styles.agentGrid}>
        {/* Analyst Agent */}
        <div style={{
          ...styles.agentCard,
          borderLeft: `3px solid ${isChain ? "#8b5cf6" : "#6b7280"}`,
        }}>
          <div style={styles.agentRole}>🔬 Analyst</div>
          <div style={styles.agentModel}>
            {isChain ? "Kimi K3" : "Gemma 4"}
          </div>
          <div style={styles.agentParams}>
            {isChain ? "2.8T params" : "31B params"}
          </div>
          <div style={styles.agentStatus}>
            <span style={{
              ...styles.statusDot,
              background: (isChain ? config.kimi_api_key_configured : config.gemini_api_key_configured)
                ? "#10b981" : "#ef4444",
            }} />
            {(isChain ? config.kimi_api_key_configured : config.gemini_api_key_configured)
              ? "API Key ✓" : "No Key ✗"}
          </div>
          {usage && (
            <div style={styles.agentCalls}>
              {isChain
                ? `${usage.kimi_calls_today} calls today`
                : `${usage.gemma_calls_today}/${usage.gemma_daily_limit} calls`}
            </div>
          )}
        </div>

        {/* Reviewer Agent */}
        <div style={{
          ...styles.agentCard,
          borderLeft: `3px solid ${isChain ? "#10b981" : "#374151"}`,
          opacity: isChain ? 1 : 0.4,
        }}>
          <div style={styles.agentRole}>🔍 Reviewer</div>
          <div style={styles.agentModel}>
            {isChain ? "Gemma 4" : "N/A"}
          </div>
          <div style={styles.agentParams}>
            {isChain ? "31B params" : "Disabled"}
          </div>
          {isChain && usage && (
            <>
              <div style={styles.reviewStats}>
                <span style={{ color: "#10b981" }}>✓{reviews.agreed || 0}</span>
                <span style={{ color: "#fbbf24" }}>⚠{reviews.cautioned || 0}</span>
                <span style={{ color: "#ef4444" }}>✗{reviews.vetoed || 0}</span>
              </div>
              <div style={styles.agentCalls}>
                {`${usage.gemma_calls_today}/${usage.gemma_daily_limit} calls`}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Always-on agents */}
      <div style={styles.alwaysOn}>
        <div style={styles.alwaysOnItem}>
          <span>🧠</span> <span>ML Engine</span>
          <span style={{ ...styles.statusDot, background: "#10b981" }} />
        </div>
        <div style={styles.alwaysOnItem}>
          <span>🛡️</span> <span>Risk Guardian</span>
          <span style={{ ...styles.statusDot, background: "#10b981" }} />
        </div>
      </div>
    </div>
  );
}

const styles = {
  card: {
    background: "rgba(17, 24, 39, 0.6)",
    backdropFilter: "blur(12px)",
    border: "1px solid rgba(75, 85, 99, 0.3)",
    borderRadius: "12px",
    padding: "16px",
    marginBottom: "16px",
  },
  cardHeader: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    marginBottom: "12px",
  },
  headerIcon: {
    fontSize: "18px",
  },
  headerTitle: {
    fontSize: "14px",
    fontWeight: 600,
    color: "#e5e7eb",
    flex: 1,
  },
  badge: {
    fontSize: "11px",
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: "9999px",
    letterSpacing: "0.03em",
  },
  toggleRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: "12px",
    padding: "8px 12px",
    background: "rgba(31, 41, 55, 0.5)",
    borderRadius: "8px",
  },
  toggleLabel: {
    fontSize: "12px",
    color: "#9ca3af",
  },
  toggleButton: {
    border: "none",
    borderRadius: "6px",
    padding: "4px 14px",
    fontSize: "12px",
    fontWeight: 600,
    color: "#fff",
    cursor: "pointer",
    transition: "all 0.2s",
  },
  agentGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "8px",
    marginBottom: "12px",
  },
  agentCard: {
    background: "rgba(31, 41, 55, 0.4)",
    borderRadius: "8px",
    padding: "10px",
    display: "flex",
    flexDirection: "column",
    gap: "4px",
  },
  agentRole: {
    fontSize: "11px",
    fontWeight: 600,
    color: "#9ca3af",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
  },
  agentModel: {
    fontSize: "13px",
    fontWeight: 600,
    color: "#e5e7eb",
  },
  agentParams: {
    fontSize: "11px",
    color: "#6b7280",
  },
  agentStatus: {
    display: "flex",
    alignItems: "center",
    gap: "4px",
    fontSize: "11px",
    color: "#9ca3af",
  },
  agentCalls: {
    fontSize: "11px",
    color: "#6b7280",
  },
  statusDot: {
    display: "inline-block",
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    flexShrink: 0,
  },
  reviewStats: {
    display: "flex",
    gap: "8px",
    fontSize: "11px",
    fontWeight: 600,
  },
  alwaysOn: {
    display: "flex",
    gap: "12px",
    padding: "8px 12px",
    background: "rgba(31, 41, 55, 0.3)",
    borderRadius: "8px",
    fontSize: "11px",
    color: "#6b7280",
  },
  alwaysOnItem: {
    display: "flex",
    alignItems: "center",
    gap: "4px",
  },
  errorText: {
    fontSize: "11px",
    color: "#ef4444",
    marginBottom: "8px",
  },
  loadingBar: {
    fontSize: "12px",
    color: "#6b7280",
    textAlign: "center",
    padding: "20px 0",
  },
};
