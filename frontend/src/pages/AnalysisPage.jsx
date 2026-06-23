import { useState } from "react";
import { AlertTriangle, CandlestickChart, Eye, ChevronDown, ChevronUp } from "lucide-react";
import { useApp } from "../context/AppContext";
import Topbar from "../components/Topbar";
import ActionPill from "../components/ActionPill";
import EmptyState from "../components/EmptyState";
import ScoreGauge from "../components/ScoreGauge";
import InteractiveChart from "../components/InteractiveChart";
import {
  money,
  probability,
  sentiment,
  dateTime,
  featureName,
  featureValue,
  signedClass,
} from "../format";

export default function AnalysisPage() {
  const {
    selectedTicker,
    selectedAnalysis,
    selectedTrades,
    marketData,
    watchlistRows,
    loadTicker,
  } = useApp();
  const [activeTab, setActiveTab] = useState("chart");
  const [brainCollapsed, setBrainCollapsed] = useState(false);

  return (
    <>
      <Topbar
        title="Quant Analysis"
        subtitle={selectedTicker ? `Analyzing ${selectedTicker}` : "Select a ticker"}
      />
      
      <div className="terminal-grid">
        {/* ── Left Column: Watchlist Scanner (Sidebar Drawer) ── */}
        <div className={`scanner-sidebar ${activeTab === "scanner" ? "mobile-show" : "mobile-hide"}`}>
          <div className="scanner-sidebar-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>Watchlist ({watchlistRows.length})</span>
            {/* Tab switcher shown on mobile inside left panel header for quick navigation */}
            <div className="segmented mobile-only-tabs">
              {["scanner", "chart", "brain"].map((tab) => (
                <button
                  key={tab}
                  className={activeTab === tab ? "active" : ""}
                  onClick={() => setActiveTab(tab)}
                  type="button"
                >
                  {tab === "scanner" ? "List" : tab === "chart" ? "Chart" : "Quant"}
                </button>
              ))}
            </div>
          </div>
          
          <div className="scanner-sidebar-list">
            {watchlistRows.map((row) => {
              const isActive = selectedTicker && row.ticker.toUpperCase() === selectedTicker.toUpperCase();
              return (
                <button
                  key={row.ticker}
                  className={`watchlist-item ${isActive ? "active" : ""}`}
                  onClick={() => {
                    loadTicker(row.ticker);
                    setActiveTab("chart"); // Slide back to chart on mobile
                  }}
                  type="button"
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <strong style={{ display: "block" }}>{row.ticker}</strong>
                    <small style={{ color: "var(--text-secondary)", fontSize: "10px" }}>
                      ML {(row.ml * 100).toFixed(0)}% • Sent {row.sentiment >= 0 ? "+" : ""}{row.sentiment.toFixed(2)}
                    </small>
                  </div>
                  <div className="watchlist-right">
                    <span style={{ fontSize: "11px", fontWeight: "600", fontFamily: "JetBrains Mono, monospace" }}>
                      {row.price ? money(row.price) : "--"}
                    </span>
                    <ActionPill action={row.action} />
                  </div>
                </button>
              );
            })}
            
            {watchlistRows.length === 0 && (
              <div style={{ padding: "20px" }}>
                <EmptyState
                  title="No Watchlist"
                  text="Connect backend to load stock watchlist."
                />
              </div>
            )}
          </div>
        </div>

        {/* ── Middle Column: Chart Panel ── */}
        <div className={`chart-panel ${activeTab === "chart" ? "mobile-show" : "mobile-hide"}`}>
          <div className="panel-title-row">
            <div>
              <h2>
                <CandlestickChart size={16} />
                {selectedTicker || "Select a Ticker"}
                {selectedAnalysis && (
                  <ActionPill action={selectedAnalysis.action} />
                )}
              </h2>
              <span style={{ display: "block", marginTop: "3px" }}>
                {selectedAnalysis
                  ? `Latest: ${dateTime(selectedAnalysis.timestamp)}`
                  : "Interactive OHLCV chart with indicator overlays"}
              </span>
            </div>
            
            {/* Tab switcher shown on mobile inside chart panel header */}
            <div className="segmented mobile-only-tabs">
              {["scanner", "chart", "brain"].map((tab) => (
                <button
                  key={tab}
                  className={activeTab === tab ? "active" : ""}
                  onClick={() => setActiveTab(tab)}
                  type="button"
                >
                  {tab === "scanner" ? "Watchlist" : tab === "chart" ? "Chart" : "Quant"}
                </button>
              ))}
            </div>
          </div>

          <InteractiveChart
            analysis={selectedAnalysis}
            bars={marketData?.bars || []}
            indicators={marketData?.indicators || {}}
            ticker={selectedTicker}
            trades={selectedTrades}
          />
        </div>

        {/* ── Right Column: Brain / Quant Panel ── */}
        <div className={`brain-panel ${brainCollapsed ? "collapsed" : ""} ${activeTab === "brain" ? "mobile-show" : "mobile-hide"}`}>
          <div className="panel-title-row compact" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: brainCollapsed ? 0 : "16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <h2 style={{ display: "flex", alignItems: "center", gap: "8px", margin: 0 }}>
                Quant Engine
                <button
                  onClick={() => setBrainCollapsed(!brainCollapsed)}
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--text-secondary)",
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    padding: "4px",
                    borderRadius: "var(--radius-sm)",
                  }}
                  className="mobile-hide"
                  title={brainCollapsed ? "Expand panel" : "Collapse panel"}
                  type="button"
                >
                  {brainCollapsed ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
              </h2>
              {!brainCollapsed && <span style={{ fontSize: "11px", color: "var(--text-secondary)" }}>Backend decision metrics, weights & thresholds</span>}
            </div>
            
            {/* Tab switcher shown on mobile inside quant panel header */}
            <div className="segmented mobile-only-tabs">
              {["scanner", "chart", "brain"].map((tab) => (
                <button
                  key={tab}
                  className={activeTab === tab ? "active" : ""}
                  onClick={() => setActiveTab(tab)}
                  type="button"
                >
                  {tab === "scanner" ? "Watchlist" : tab === "chart" ? "Chart" : "Quant"}
                </button>
              ))}
            </div>
          </div>

          {!brainCollapsed && (
            <>
              {/* Score Gauges */}
              <div className="score-grid" style={{ marginTop: "12px", marginBottom: "12px" }}>
                <ScoreGauge
                  label="Final Score"
                  value={Number(selectedAnalysis?.final_score || 0)}
                  min={0}
                  max={1}
                  threshold={0.6}
                />
                <ScoreGauge
                  label="LLM Conf"
                  value={Number(selectedAnalysis?.gemini_confidence || 0)}
                  min={0}
                  max={1}
                  threshold={0.6}
                />
                <ScoreGauge
                  label="ML Prob"
                  value={Number(selectedAnalysis?.ml_confidence || 0)}
                  min={0}
                  max={1}
                  threshold={0.55}
                />
                <ScoreGauge
                  label="News Sentiment"
                  value={Number(selectedAnalysis?.gemini_sentiment_score || 0)}
                  min={-1}
                  max={1}
                  threshold={0}
                  sentimentMode
                />
              </div>

              {/* Crisis Alert */}
              {selectedAnalysis?.crisis_detected && (
                <div className="matrix-cell sell" style={{ margin: "10px 0", display: "flex", gap: 6, alignItems: "center" }}>
                  <AlertTriangle size={14} />
                  <strong>CRISIS MODE — Trades Restricted</strong>
                </div>
              )}

              {/* Pipeline Steps */}
              <div className="section-label" style={{ marginTop: "16px" }}>Decision Pipeline</div>
              <div className="pipeline">
                {[
                  { label: "Stage 1", value: "Bulk screener (RSI+Vol)" },
                  { label: "Stage 2", value: "Sentiment Indexing" },
                  { label: "Stage 3", value: "XGBoost ensemble" },
                  { label: "Stage 4", value: "LLM Review" },
                  { label: "Stage 5", value: "Risk manager" },
                  { label: "Final", value: "Decision Matrix" },
                ].map(({ label, value }) => (
                  <div className="pipeline-step" key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>

              {/* Execution Matrix */}
              <div className="section-label" style={{ marginTop: "16px" }}>Decision Matrix</div>
              <div className="matrix">
                <div className="matrix-cell buy">
                  <span>BUY</span>
                  <strong>Score ≥ 0.60 + No Crisis + Risk OK</strong>
                </div>
                <div className="matrix-cell sell">
                  <span>SELL</span>
                  <strong>Score &lt; 0.30 OR Crisis OR Stop-Loss</strong>
                </div>
                <div className="matrix-cell hold">
                  <span>HOLD</span>
                  <strong>In between — flat position</strong>
                </div>
              </div>

              {/* Strategy Decision Proof */}
              <div className="reason-box">
                <span>Strategy Decision Proof</span>
                <p>
                  {selectedAnalysis?.action_reason ||
                    "No analysis decision has been logged for this ticker yet."}
                </p>
              </div>

              {/* Risk Factors */}
              {selectedAnalysis?.gemini_risk_factors?.length > 0 && (
                <div className="reason-box" style={{ borderColor: "rgba(242, 54, 69, 0.25)", background: "var(--red-soft)" }}>
                  <span className="negative">⚠ Risk Factors</span>
                  <ul
                    style={{
                      margin: "6px 0 0 0",
                      paddingLeft: "16px",
                      fontSize: "12px",
                      color: "var(--text-secondary)",
                      lineHeight: "1.4",
                    }}
                  >
                    {selectedAnalysis.gemini_risk_factors.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* ML Features */}
              <div className="feature-list">
                <div className="section-label">ML Features Used</div>
                {Object.entries(selectedAnalysis?.ml_features_used || marketData?.indicators || {})
                  .slice(0, 8)
                  .map(([key, val]) => (
                    <div className="feature-row" key={key}>
                      <span>{featureName(key)}</span>
                      <div className="feature-meter">
                        <i style={{ width: `${calcFeatureWidth(key, val)}%` }} />
                      </div>
                      <strong>{featureValue(val)}</strong>
                    </div>
                  ))}
                {Object.keys(selectedAnalysis?.ml_features_used || marketData?.indicators || {}).length === 0 && (
                  <div style={{ color: "var(--muted)", fontSize: "11px", padding: "4px 0" }}>
                    No features loaded.
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

function calcFeatureWidth(key, value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 8;
  const lower = key.toLowerCase();
  if (lower.includes("rsi")) return Math.min(100, Math.max(0, num));
  if (lower.includes("spike") || lower.includes("ratio")) return Math.min(100, Math.max(0, num * 45));
  if (lower.includes("confidence")) return Math.min(100, Math.max(0, num * 100));
  return Math.min(100, Math.max(8, Math.abs(num) % 100));
}
