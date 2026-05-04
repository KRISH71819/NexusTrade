import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BarChart3,
  Brain,
  CandlestickChart,
  CircleDollarSign,
  Database,
  History,
  Loader2,
  Newspaper,
  Play,
  RefreshCcw,
  Search,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { api, apiBase } from "./api";
import {
  actionClass,
  dateTime,
  featureName,
  featureValue,
  money,
  normalizeTicker,
  percent,
  probability,
  sentiment,
  signedClass,
} from "./format";
import InteractiveChart from "./components/InteractiveChart.jsx";
import ScoreGauge from "./components/ScoreGauge.jsx";

const emptyDashboard = {
  portfolio: null,
  portfolioHistory: [],
  analyses: [],
  trades: [],
  watchlist: [],
  watchlistInfo: null,
};

export default function App() {
  const [dashboard, setDashboard] = useState(emptyDashboard);
  const [selectedTicker, setSelectedTicker] = useState("");
  const [marketData, setMarketData] = useState(null);
  const [analysisHistory, setAnalysisHistory] = useState([]);
  const [activeTab, setActiveTab] = useState("chart");
  const [capitalAmount, setCapitalAmount] = useState("1000000");
  const [status, setStatus] = useState({
    loading: true,
    action: "",
    online: false,
    error: "",
    message: "",
  });

  const selectedAnalysis = useMemo(() => {
    if (analysisHistory.length) return analysisHistory[0];
    return (
      dashboard.analyses.find(
        (item) => normalizeTicker(item.ticker) === normalizeTicker(selectedTicker),
      ) || null
    );
  }, [analysisHistory, dashboard.analyses, selectedTicker]);

  const selectedTrades = useMemo(
    () =>
      dashboard.trades.filter(
        (trade) => normalizeTicker(trade.ticker) === normalizeTicker(selectedTicker),
      ),
    [dashboard.trades, selectedTicker],
  );

  const watchlistRows = useMemo(
    () => buildWatchlistRows(dashboard.watchlist, dashboard.analyses),
    [dashboard.watchlist, dashboard.analyses],
  );

  const loadTicker = useCallback(async (ticker) => {
    if (!ticker) return;

    setSelectedTicker(ticker);
    setStatus((current) => ({ ...current, action: "Loading ticker", error: "" }));

    try {
      const [market, history] = await Promise.all([
        api.marketData(ticker),
        api.analysisHistory(ticker, 80),
      ]);
      setMarketData(market || null);
      setAnalysisHistory(history?.analyses || []);
    } catch (error) {
      setMarketData(null);
      setAnalysisHistory([]);
      setStatus((current) => ({
        ...current,
        error: `Could not load ${ticker}. ${error.message}`,
      }));
    } finally {
      setStatus((current) => ({ ...current, action: "" }));
    }
  }, []);

  const loadDashboard = useCallback(
    async ({ quiet = false, keepTicker = "" } = {}) => {
      setStatus((current) => ({
        ...current,
        loading: !quiet,
        action: quiet ? "Refreshing" : "Connecting",
        error: "",
        message: "",
      }));

      try {
        const [health, watchlistInfo, portfolio, portfolioHistory, analyses, trades] =
          await Promise.all([
            api.health(),
            api.watchlist(),
            api.portfolio(),
            api.portfolioHistory(120),
            api.latestAnalyses(),
            api.trades(150),
          ]);

        const nextDashboard = {
          portfolio,
          portfolioHistory: portfolioHistory?.snapshots || [],
          analyses: analyses?.analyses || [],
          trades: trades?.trades || [],
          watchlist: watchlistInfo?.watchlist || [],
          watchlistInfo,
        };
        setDashboard(nextDashboard);
        setCapitalAmount(String(nextDashboard.portfolio?.initial_balance || 1000000));

        const nextTicker =
          keepTicker ||
          nextDashboard.analyses[0]?.ticker ||
          nextDashboard.portfolio?.holdings?.[0]?.ticker ||
          nextDashboard.watchlist[0] ||
          "";

        setStatus({
          loading: false,
          action: "",
          online: Boolean(health),
          error: "",
          message: quiet ? "Dashboard refreshed from backend." : "",
        });

        if (nextTicker) {
          await loadTicker(nextTicker);
        }
      } catch (error) {
        setDashboard(emptyDashboard);
        setMarketData(null);
        setAnalysisHistory([]);
        setStatus({
          loading: false,
          action: "",
          online: false,
          error: `Backend unavailable at ${apiBase}. ${error.message}`,
          message: "",
        });
      }
    },
    [loadTicker],
  );

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  const runAnalysis = async () => {
    setStatus((current) => ({ ...current, action: "Running analysis", message: "", error: "" }));
    try {
      const result = await api.triggerAnalysis();
      setStatus((current) => ({
        ...current,
        message: `Analysis completed: ${(result.results || []).length} tickers processed.`,
      }));
      await loadDashboard({ quiet: true, keepTicker: selectedTicker });
    } catch (error) {
      setStatus((current) => ({
        ...current,
        error: `Analysis trigger failed. ${error.message}`,
      }));
    } finally {
      setStatus((current) => ({ ...current, action: "" }));
    }
  };

  const refreshMarket = async () => {
    setStatus((current) => ({ ...current, action: "Refreshing market", message: "", error: "" }));
    try {
      const result = await api.refreshMarket();
      setStatus((current) => ({
        ...current,
        message: `Market cache refreshed: ${Number(result.tickers_updated || 0)} tickers updated.`,
      }));
      await loadDashboard({ quiet: true, keepTicker: selectedTicker });
    } catch (error) {
      setStatus((current) => ({
        ...current,
        error: `Market refresh failed. ${error.message}`,
      }));
    } finally {
      setStatus((current) => ({ ...current, action: "" }));
    }
  };

  const resetPaperAccount = async () => {
    const amount = Number(capitalAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      setStatus((current) => ({ ...current, error: "Enter a valid starting capital amount." }));
      return;
    }

    setStatus((current) => ({ ...current, action: "Resetting portfolio", message: "", error: "" }));
    try {
      await api.resetPortfolio(amount, true);
      setStatus((current) => ({
        ...current,
        message: `Portfolio reset to ${money(amount)}. Trades, analysis log, and P&L history were cleared.`,
      }));
      await loadDashboard({ quiet: true });
    } catch (error) {
      setStatus((current) => ({
        ...current,
        error: `Portfolio reset failed. ${error.message}`,
      }));
    } finally {
      setStatus((current) => ({ ...current, action: "" }));
    }
  };

  const counts = useMemo(() => countActions(dashboard.analyses), [dashboard.analyses]);
  const portfolio = dashboard.portfolio || {};
  const holdings = portfolio.holdings || [];
  const isBusy = status.loading || Boolean(status.action);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">NT</div>
          <div>
            <strong>NexusTrade AI</strong>
            <span>Autonomous paper hedge fund</span>
          </div>
        </div>

        <div className="market-state">
          <span className={`status-dot ${status.online ? "online" : "offline"}`} />
          <div>
            <strong>{status.online ? "Live FastAPI" : "Backend offline"}</strong>
            <small>
              {dashboard.watchlistInfo
                ? `${dashboard.watchlistInfo.market} | ${dashboard.watchlistInfo.market_hours}`
                : `API ${apiBase}`}
            </small>
          </div>
        </div>

        <div className="watchlist-search">
          <Search size={16} />
          <span>{watchlistRows.length} backend tickers</span>
        </div>

        <div className="watchlist">
          {watchlistRows.length ? (
            watchlistRows.map((row) => (
              <button
                className={`watchlist-item ${
                  normalizeTicker(row.ticker) === normalizeTicker(selectedTicker) ? "active" : ""
                }`}
                key={row.ticker}
                onClick={() => loadTicker(row.ticker)}
                type="button"
              >
                <div>
                  <strong>{row.ticker}</strong>
                  <small>
                    ML {probability(row.ml)} | Sent {sentiment(row.sentiment)}
                  </small>
                </div>
                <div className="watchlist-right">
                  <ActionPill action={row.action} />
                  <span>{row.price ? money(row.price) : "--"}</span>
                </div>
              </button>
            ))
          ) : (
            <EmptyBlock title="No watchlist loaded" text="The backend watchlist endpoint returned no tickers." />
          )}
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <div className="ticker-line">
              <h1>{selectedTicker || "NexusTrade AI"}</h1>
              <ActionPill action={selectedAnalysis?.action || "HOLD"} />
            </div>
            <p>
              {selectedAnalysis
                ? `Latest AI decision: ${dateTime(selectedAnalysis.timestamp)}`
                : "Waiting for backend analysis output"}
            </p>
          </div>

          <div className="toolbar">
            <div className="api-chip">
              <Database size={15} />
              <span>{apiBase}</span>
            </div>
            <label className="capital-control">
              <span>Capital</span>
              <input
                min="1"
                onChange={(event) => setCapitalAmount(event.target.value)}
                step="10000"
                type="number"
                value={capitalAmount}
              />
            </label>
            <button className="button secondary" disabled={isBusy || !status.online} onClick={resetPaperAccount}>
              <RefreshCcw size={16} />
              Reset
            </button>
            <button
              className="button secondary"
              disabled={isBusy}
              onClick={() => loadDashboard({ quiet: true, keepTicker: selectedTicker })}
            >
              <RefreshCcw size={16} />
              Refresh
            </button>
            <button className="button secondary" disabled={isBusy || !status.online} onClick={refreshMarket}>
              <BarChart3 size={16} />
              Refresh market
            </button>
            <button className="button primary" disabled={isBusy || !status.online} onClick={runAnalysis}>
              {status.action === "Running analysis" ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
              Run analysis
            </button>
          </div>
        </header>

        {(status.error || status.message || status.action) && (
          <div className={`notice ${status.error ? "error" : ""}`}>
            {status.action && <Loader2 className="spin" size={16} />}
            <span>{status.error || status.message || status.action}</span>
          </div>
        )}

        <section className="metrics-grid">
          <MetricCard
            icon={<CircleDollarSign size={18} />}
            label="Total Value"
            value={money(portfolio.total_value)}
            sub={`${money(portfolio.total_pnl)} (${percent(portfolio.total_pnl_pct)})`}
            trend={portfolio.total_pnl}
          />
          <MetricCard
            icon={<Wallet size={18} />}
            label="Cash"
            value={money(portfolio.cash)}
            sub={`${percent(portfolio.total_value ? (portfolio.cash / portfolio.total_value) * 100 : 0)} liquid`}
          />
          <MetricCard
            icon={<Activity size={18} />}
            label="Invested"
            value={money(portfolio.holdings_value)}
            sub={`${holdings.length} open holdings`}
          />
          <MetricCard
            icon={<Brain size={18} />}
            label="AI Decisions"
            value={String(dashboard.analyses.length)}
            sub={`BUY ${counts.BUY} / SELL ${counts.SELL} / HOLD ${counts.HOLD}`}
          />
        </section>

        <section className="terminal-grid">
          <div className="chart-panel">
            <div className="panel-title-row">
              <div>
                <h2>
                  <CandlestickChart size={18} />
                  Interactive Price Terminal
                </h2>
                <span>Mouse wheel zoom, drag pan, crosshair OHLC, volume, SMA overlays, trade markers</span>
              </div>
              <div className="segmented">
                {["chart", "scanner", "ledger"].map((tab) => (
                  <button
                    className={activeTab === tab ? "active" : ""}
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    type="button"
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
              <Scanner rows={watchlistRows} onPick={(ticker) => {
                setActiveTab("chart");
                loadTicker(ticker);
              }} />
            )}
            {activeTab === "ledger" && <TradeLedger trades={dashboard.trades} />}
          </div>

          <aside className="brain-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <Brain size={18} />
                  AI Brain
                </h2>
                <span>Every decision is shown with thresholds and payloads</span>
              </div>
            </div>

            <div className="score-grid">
              <ScoreGauge
                label="ML trend probability"
                max={1}
                min={0}
                threshold={0.65}
                value={Number(selectedAnalysis?.ml_confidence || 0)}
              />
              <ScoreGauge
                label="Gemini news sentiment"
                max={1}
                min={-1}
                threshold={0.3}
                value={Number(selectedAnalysis?.gemini_sentiment_score || 0)}
                sentimentMode
              />
            </div>

            <div className="pipeline">
              <PipelineStep label="Stage 1" value="Technical pre-screen" />
              <PipelineStep label="Stage 2A" value="XGBoost quant" />
              <PipelineStep label="Stage 2B" value="Gemini analyst" />
              <PipelineStep label="Decision" value="Execution matrix" />
            </div>

            <div className="matrix">
              <MatrixCell tone="buy" label="BUY gate" value="ML > 65% + Sent > +0.30" />
              <MatrixCell tone="sell" label="SELL gate" value="ML < 35% + Sent < -0.30" />
              <MatrixCell tone="hold" label="Fallback" value="HOLD until proof is strong" />
            </div>

            <div className="reason-box">
              <span>Decision proof</span>
              <p>{selectedAnalysis?.action_reason || "No analysis decision has been logged for this ticker yet."}</p>
            </div>

            <FeatureList features={selectedAnalysis?.ml_features_used || marketData?.indicators || {}} />
          </aside>
        </section>

        <section className="detail-grid">
          <NewsPanel analysis={selectedAnalysis} />
          <HoldingsPanel holdings={holdings} cash={portfolio.cash} />
          <AuditPanel analyses={analysisHistory.length ? analysisHistory : dashboard.analyses} />
        </section>
      </main>
    </div>
  );
}

function buildWatchlistRows(watchlist, analyses) {
  const rows = analyses.map((analysis) => ({
    ticker: analysis.ticker,
    price: Number(analysis.current_price || analysis.price || 0),
    ml: Number(analysis.ml_confidence || 0),
    sentiment: Number(analysis.gemini_sentiment_score || 0),
    action: analysis.action || "HOLD",
  }));

  const known = new Set(rows.map((row) => normalizeTicker(row.ticker)));
  const fillers = watchlist
    .filter((ticker) => !known.has(normalizeTicker(ticker)))
    .map((ticker) => ({ ticker, price: 0, ml: 0, sentiment: 0, action: "HOLD" }));

  return [...rows, ...fillers].sort((a, b) => b.ml - a.ml || a.ticker.localeCompare(b.ticker));
}

function countActions(analyses) {
  return analyses.reduce(
    (acc, item) => {
      const action = item.action || "HOLD";
      acc[action] = (acc[action] || 0) + 1;
      return acc;
    },
    { BUY: 0, SELL: 0, HOLD: 0 },
  );
}

function ActionPill({ action }) {
  return <span className={`action-pill ${actionClass(action)}`}>{action || "HOLD"}</span>;
}

function MetricCard({ icon, label, value, sub, trend }) {
  return (
    <article className="metric">
      <div className="metric-label">
        {icon}
        <span>{label}</span>
      </div>
      <strong>{value}</strong>
      <small className={trend === undefined ? "" : signedClass(trend)}>{sub}</small>
    </article>
  );
}

function PipelineStep({ label, value }) {
  return (
    <div className="pipeline-step">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function MatrixCell({ tone, label, value }) {
  return (
    <div className={`matrix-cell ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function FeatureList({ features }) {
  const entries = Object.entries(features).slice(0, 10);

  return (
    <div className="feature-list">
      <div className="section-label">ML Features Used</div>
      {entries.length ? (
        entries.map(([key, value]) => (
          <div className="feature-row" key={key}>
            <span>{featureName(key)}</span>
            <div className="feature-meter">
              <i style={{ width: `${featureWidth(key, value)}%` }} />
            </div>
            <strong>{featureValue(value)}</strong>
          </div>
        ))
      ) : (
        <EmptyBlock title="No feature payload" text="Run backend analysis to populate XGBoost inputs." />
      )}
    </div>
  );
}

function featureWidth(key, value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 8;
  const lower = key.toLowerCase();
  if (lower.includes("rsi")) return clamp(number, 0, 100);
  if (lower.includes("spike") || lower.includes("ratio")) return clamp(number * 45, 0, 100);
  if (lower.includes("confidence")) return clamp(number * 100, 0, 100);
  return clamp(Math.abs(number) % 100, 8, 100);
}

function Scanner({ rows, onPick }) {
  return (
    <div className="scanner-grid">
      {rows.length ? (
        rows.map((row) => (
          <button className="scanner-card" key={row.ticker} onClick={() => onPick(row.ticker)} type="button">
            <div className="scanner-card-top">
              <strong>{row.ticker}</strong>
              <ActionPill action={row.action} />
            </div>
            <div className="scanner-metrics">
              <ScannerMetric label="Price" value={row.price ? money(row.price) : "--"} />
              <ScannerMetric label="ML" value={probability(row.ml)} />
              <ScannerMetric label="Sentiment" value={sentiment(row.sentiment)} tone={signedClass(row.sentiment)} />
              <ScannerMetric label="Proof" value={proofLabel(row)} />
            </div>
          </button>
        ))
      ) : (
        <EmptyBlock title="No scanner rows" text="No watchlist or latest analysis records came from the backend." />
      )}
    </div>
  );
}

function ScannerMetric({ label, value, tone = "" }) {
  return (
    <div className="scanner-metric">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function proofLabel(row) {
  if (row.action === "BUY") return "Confirmed";
  if (row.action === "SELL") return "Risk-off";
  if (row.ml > 0.55 || Math.abs(row.sentiment) > 0.2) return "Watching";
  return "Neutral";
}

function TradeLedger({ trades }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Ticker</th>
            <th>Action</th>
            <th>Qty</th>
            <th>Price</th>
            <th>ML</th>
            <th>Sentiment</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {trades.length ? (
            trades.map((trade, index) => (
              <tr key={`${trade.timestamp}-${trade.ticker}-${index}`}>
                <td>{dateTime(trade.timestamp)}</td>
                <td>{trade.ticker}</td>
                <td>
                  <ActionPill action={trade.action} />
                </td>
                <td>{Number(trade.quantity || 0).toLocaleString("en-IN")}</td>
                <td>{money(trade.price)}</td>
                <td>{probability(trade.ml_confidence)}</td>
                <td className={signedClass(trade.gemini_sentiment_score)}>
                  {sentiment(trade.gemini_sentiment_score)}
                </td>
                <td>{money(trade.total_value)}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan="8">No BUY or SELL trades returned by the backend yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function NewsPanel({ analysis }) {
  const headlines = analysis?.news_headlines || [];

  return (
    <article className="detail-panel">
      <div className="panel-title-row compact">
        <div>
          <h2>
            <Newspaper size={18} />
            News Sentiment
          </h2>
          <span>Headlines sent into the Gemini analyst</span>
        </div>
      </div>
      <div className="news-list">
        {headlines.length ? (
          headlines.slice(0, 3).map((headline, index) => (
            <div className="news-item" key={`${headline}-${index}`}>
              <strong>
                <span>{index + 1}</span>
                {headline || "Untitled headline"}
              </strong>
              <small>Gemini sentiment input</small>
            </div>
          ))
        ) : (
          <EmptyBlock title="No news headlines" text="The selected ticker has no analysis news payload yet." />
        )}
      </div>
      <div className="reason-box analyst">
        <span>Gemini explanation</span>
        <p>{analysis?.gemini_explanation || "No LLM explanation has been logged yet."}</p>
      </div>
    </article>
  );
}

function HoldingsPanel({ holdings, cash }) {
  return (
    <article className="detail-panel">
      <div className="panel-title-row compact">
        <div>
          <h2>
            <Wallet size={18} />
            Portfolio Holdings
          </h2>
          <span>Virtual positions from MongoDB portfolio state</span>
        </div>
      </div>
      <div className="holdings-list">
        {holdings.length ? (
          holdings.map((holding) => (
            <div className="holding-row" key={holding.ticker}>
              <div>
                <strong>{holding.ticker}</strong>
                <small>
                  {holding.quantity} shares at avg {money(holding.avg_price)}
                </small>
              </div>
              <div className="holding-values">
                <strong>{money(holding.market_value)}</strong>
                <small className={signedClass(holding.unrealized_pnl)}>
                  {money(holding.unrealized_pnl)} ({percent(holding.unrealized_pnl_pct)})
                </small>
              </div>
            </div>
          ))
        ) : (
          <div className="holding-row">
            <div>
              <strong>No open holdings</strong>
              <small>The backend portfolio is fully liquid.</small>
            </div>
            <div className="holding-values">
              <strong>{money(cash)}</strong>
              <small>Cash</small>
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

function AuditPanel({ analyses }) {
  const items = [...analyses].sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0));

  return (
    <article className="detail-panel audit-panel">
      <div className="panel-title-row compact">
        <div>
          <h2>
            <History size={18} />
            Audit Trail
          </h2>
          <span>Latest backend analysis records, including HOLDs</span>
        </div>
      </div>
      <div className="timeline">
        {items.length ? (
          items.slice(0, 14).map((item, index) => (
            <div className={`timeline-item ${actionClass(item.action)}`} key={`${item.timestamp}-${item.ticker}-${index}`}>
              <small>{dateTime(item.timestamp)}</small>
              <strong>
                {item.action || "HOLD"} {item.ticker} at {money(item.current_price || item.price)}
              </strong>
              <p>{item.action_reason || "No action reason was included in this backend record."}</p>
            </div>
          ))
        ) : (
          <EmptyBlock title="No audit records" text="Run the backend scheduler or manual analysis to populate analysis_log." />
        )}
      </div>
    </article>
  );
}

function EmptyBlock({ title, text }) {
  return (
    <div className="empty-block">
      <ShieldCheck size={16} />
      <strong>{title}</strong>
      <span>{text}</span>
    </div>
  );
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
