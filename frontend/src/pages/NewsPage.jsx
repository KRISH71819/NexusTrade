import { useEffect, useRef, useMemo } from "react";
import {
  AlertTriangle,
  Globe,
  Newspaper,
} from "lucide-react";
import { gsap } from "gsap";
import { useApp } from "../context/AppContext";
import { useNavigate } from "react-router-dom";
import Topbar from "../components/Topbar";
import EmptyState from "../components/EmptyState";
import { signedClass } from "../format";

export default function NewsPage() {
  const { dashboard, selectedAnalysis, selectedTicker, watchlistRows, loadTicker } = useApp();
  const navigate = useNavigate();
  const news = dashboard.news;
  const macroNews = news?.macro_news || [];
  const tickerNews = news?.ticker_news || [];
  const crisisAlerts = news?.crisis_alerts || [];

  const displayTickerNews = useMemo(() => {
    const hasScores = tickerNews.some(t => (t.overall_news_score || 0) !== 0);
    if (!hasScores && watchlistRows.length > 0) {
      return [...watchlistRows].map(r => ({
        ticker: r.ticker,
        overall_news_score: r.sentiment,
        sector: dashboard.analyses.find(a => a.ticker === r.ticker)?.sector || "Sector",
        crisis_detected: dashboard.analyses.find(a => a.ticker === r.ticker)?.crisis_detected || false,
      }));
    }
    return tickerNews;
  }, [tickerNews, watchlistRows, dashboard.analyses]);
  const headlines = selectedAnalysis?.news_headlines || [];
  const contentRef = useRef(null);

  useEffect(() => {
    if (contentRef.current) {
      gsap.fromTo(
        contentRef.current.children,
        { opacity: 0, y: 20 },
        {
          opacity: 1,
          y: 0,
          stagger: 0.06,
          duration: 0.5,
          ease: "power3.out",
        }
      );
    }
  }, [news]);

  return (
    <>
      <Topbar title="Market News" subtitle="Multi-level: Global → Sector → Stock" />
      <div ref={contentRef}>
        {/* Crisis Alerts */}
        {crisisAlerts.length > 0 && (
          <div className="notice error" style={{ marginBottom: "16px" }}>
            <AlertTriangle size={16} />
            <div>
              <strong>Crisis Alerts Active</strong>
              {crisisAlerts.map((a, i) => (
                <div key={i} style={{ marginTop: 4, fontSize: "13px" }}>
                  <strong
                    style={{ cursor: "pointer", textDecoration: "underline", color: "var(--accent)" }}
                    onClick={() => {
                      loadTicker(a.ticker);
                      navigate("/");
                    }}
                    title={`Inspect ${a.ticker}`}
                  >
                    {a.ticker}:
                  </strong>{" "}
                  {a.reason}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="detail-grid">
          {/* Macro News */}
          <div className="detail-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <Globe size={16} />
                  Global & India Macro ({macroNews.length})
                </h2>
                <span>Market-wide headlines affecting all tickers</span>
              </div>
            </div>
            <div className="news-list" style={{ maxHeight: 500, overflowY: "auto", marginTop: "12px" }}>
              {macroNews.length > 0 ? (
                macroNews.slice(0, 15).map((item, i) => (
                  <div className="news-item" key={`macro-${i}`}>
                    <strong>
                      <span style={{
                        display: "inline-grid",
                        placeItems: "center",
                        width: "20px",
                        height: "20px",
                        marginRight: "6px",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--accent-soft)",
                        color: "var(--accent)",
                        fontSize: "10px",
                        fontWeight: 900,
                      }}>M</span>
                      {typeof item === "string" ? item : item.headline || "Untitled"}
                    </strong>
                    <small>
                      {typeof item === "object" ? item.source || "News" : "Google News"}
                    </small>
                  </div>
                ))
              ) : (
                <EmptyState title="No macro news" text="Run analysis to fetch global news." />
              )}
            </div>
          </div>

          {/* Stock Headlines */}
          <div className="detail-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <Newspaper size={16} />
                  {selectedTicker || "Stock"} Headlines
                </h2>
                <span>Ticker-specific headlines used by strategy</span>
              </div>
            </div>
            <div className="news-list" style={{ maxHeight: 500, overflowY: "auto", marginTop: "12px" }}>
              {headlines.length > 0 ? (
                headlines.slice(0, 10).map((h, i) => (
                  <div className="news-item" key={`headline-${i}`}>
                    <strong>
                      <span style={{
                        display: "inline-grid",
                        placeItems: "center",
                        width: "20px",
                        height: "20px",
                        marginRight: "6px",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--accent-soft)",
                        color: "var(--accent)",
                        fontSize: "10px",
                        fontWeight: 900,
                      }}>{i + 1}</span>
                      {h || "Untitled headline"}
                    </strong>
                    <small>LLM analysis input</small>
                  </div>
                ))
              ) : (
                <EmptyState title="No headlines" text="Select a ticker and run analysis." />
              )}

              {/* LLM Explanation */}
              {selectedAnalysis?.gemini_explanation && (
                <div style={{ marginTop: "16px" }}>
                  <div className="reason-box">
                    <span>LLM Structured Analysis</span>
                    <p>{selectedAnalysis.gemini_explanation}</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Per-Ticker News Scores */}
        {displayTickerNews.length > 0 && (
          <div className="detail-panel" style={{ marginTop: "16px" }}>
            <div className="panel-title-row compact">
              <div>
                <h2>📊 Per-Ticker News Scores ({displayTickerNews.length})</h2>
                <span>Sentiment scores computed from news analysis</span>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px", marginTop: "12px" }}>
              {displayTickerNews.slice(0, 24).map((t, i) => (
                <div
                  className="news-item"
                  key={i}
                  style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", cursor: "pointer" }}
                  onClick={() => {
                    loadTicker(t.ticker);
                    navigate("/");
                  }}
                  title={`Inspect ${t.ticker}`}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <strong>{t.ticker}</strong>
                    {t.crisis_detected && <span className="action-pill sell" style={{ fontSize: "9px" }}>CRISIS</span>}
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--muted)", marginTop: "2px" }}>
                    {t.sector}
                  </div>
                  <div style={{ marginTop: "8px", fontSize: "13px" }}>
                    Score:{" "}
                    <strong className={signedClass(t.overall_news_score)} style={{ fontFamily: "JetBrains Mono, monospace" }}>
                      {t.overall_news_score >= 0 ? "+" : ""}{t.overall_news_score?.toFixed(3) || "0.000"}
                    </strong>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {!news && (
          <div className="detail-panel" style={{ marginTop: "16px" }}>
            <EmptyState
              icon={<Newspaper size={24} />}
              title="News not loaded"
              text="Run an analysis cycle to fetch global news intelligence."
            />
          </div>
        )}
      </div>
    </>
  );
}
