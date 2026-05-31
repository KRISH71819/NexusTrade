import { useState } from "react";
import { AlertTriangle, CandlestickChart } from "lucide-react";
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

  return (
    <>
      <Topbar
        title="Quant Analysis"
        subtitle={selectedTicker ? `Analyzing ${selectedTicker}` : "Select a ticker"}
      />
      
      <div className="terminal-grid">
        {/* ── Left: Chart / Scanner Panel ──────────────────── */}
        <div className="chart-panel">
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
                  : "Interactive OHLCV chart with SMA overlays"}
              </span>
            </div>
            <div className="segmented">
              {["chart", "scanner"].map((tab) => (
                <button
                  key={tab}
                  className={activeTab === tab ? "active" : ""}
                  onClick={() => setActiveTab(tab)}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>

          {activeTab === "chart" && (
            <InteractiveChart
              analysis={selectedAnalysis}
              bars={marketData?.bars || []}
              indicators={marketData?.indicators || {}}
              ticker={selectedTicker}
              trades={selectedTrades}
            />
          )}

          {activeTab === "scanner" && (
            <div className="scanner-grid">
              {watchlistRows.map((row) => (
                <button
                  className="scanner-card"
                  key={row.ticker}
                  onClick={() => {
                    setActiveTab("chart");
                    loadTicker(row.ticker);
                  }}
                  type="button"
                >
                  <div className="scanner-card-top">
                    <strong>{row.ticker}</strong>
                    <ActionPill action={row.action} />
                  </div>
                  <div className="scanner-metrics">
                    <div className="scanner-metric">
                      <span>Price</span>
                      <strong>{row.price ? money(row.price) : "--"}</strong>
                    </div>
                    <div className="scanner-metric">
                      <span>ML</span>
                      <strong>{probability(row.ml)}</strong>
                    </div>
                    <div className="scanner-metric">
                      <span>Sentiment</span>
                      <strong className={signedClass(row.sentiment)}>
                        {sentiment(row.sentiment)}
                      </strong>
                    </div>
                    <div className="scanner-metric">
                      <span>Signal</span>
                      <strong>
                        {row.action === "BUY"
                          ? "Confirmed"
                          : row.action === "SELL"
                          ? "Risk-off"
                          : "Neutral"}
                      </strong>
                    </div>
                  </div>
                </button>
              ))}
              {watchlistRows.length === 0 && (
                <div style={{ gridColumn: "1 / -1", padding: "20px" }}>
                  <EmptyState
                    title="No Watchlist"
                    text="Connect backend to load stock watchlist."
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Right: Brain / Quant Panel ──────────────────── */}
        <div className="brain-panel">
          <div className="panel-title-row compact">
            <div>
              <h2>Quant Engine</h2>
              <span>Backend decision metrics, weights & thresholds</span>
            </div>
          </div>

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
            <div className="reason-box" style={{ borderColor: "rgba(255, 23, 68, 0.25)", background: "var(--red-soft)" }}>
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
