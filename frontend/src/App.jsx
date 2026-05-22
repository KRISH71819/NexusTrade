import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Brain,
  CandlestickChart,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Database,
  Globe,
  History,
  Loader2,
  Newspaper,
  Play,
  RefreshCcw,
  Search,
  Shield,
  ShieldCheck,
  ShieldAlert,
  Target,
  TrendingDown,
  TrendingUp,
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

export default function App() {
  const [dashboard, setDashboard] = useState(emptyDashboard);
  const [selectedTicker, setSelectedTicker] = useState("");
  const [marketData, setMarketData] = useState(null);
  const [analysisHistory, setAnalysisHistory] = useState([]);
  const [activeTab, setActiveTab] = useState("chart");
  const [capitalAmount, setCapitalAmount] = useState("1000000");
  const [pnlData, setPnlData] = useState(emptyPnl);
  const [tradeHistory, setTradeHistory] = useState({ trades: [], total_count: 0, page: 1, total_pages: 1 });
  const [tradeHistoryPage, setTradeHistoryPage] = useState(1);
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

        setPnlData(pnl || emptyPnl);

        const nextDashboard = {
          portfolio,
          portfolioHistory: portfolioHistory?.snapshots || [],
          analyses: analyses?.analyses || [],
          trades: trades?.trades || [],
          watchlist: watchlistInfo?.watchlist || [],
          watchlistInfo,
          news,
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

  // Fetch trade history when page changes or on tab switch
  useEffect(() => {
    if (activeTab === "trades") {
      api.tradeHistory(tradeHistoryPage, 30).then(setTradeHistory).catch(() => {});
    }
  }, [activeTab, tradeHistoryPage]);

  const counts = useMemo(() => countActions(dashboard.analyses), [dashboard.analyses]);
  const portfolio = dashboard.portfolio || {};
  const holdings = portfolio.holdings || [];
  const isBusy = status.loading || Boolean(status.action);
  const riskStatus = portfolio.risk_status || {};

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
            label="Portfolio Value"
            value={money(portfolio.total_value)}
            sub={`${money(pnlData.total_pnl)} (${percent(pnlData.total_pnl_pct)})`}
            trend={pnlData.total_pnl}
            glow
          />
          <MetricCard
            icon={pnlData.daily_pnl.value >= 0 ? <TrendingUp size={18} /> : <TrendingDown size={18} />}
            label="Daily P&L"
            value={money(pnlData.daily_pnl.value)}
            sub={percent(pnlData.daily_pnl.pct)}
            trend={pnlData.daily_pnl.value}
            glow
          />
          <MetricCard
            icon={pnlData.weekly_pnl.value >= 0 ? <ArrowUpRight size={18} /> : <ArrowDownRight size={18} />}
            label="Weekly P&L"
            value={money(pnlData.weekly_pnl.value)}
            sub={percent(pnlData.weekly_pnl.pct)}
            trend={pnlData.weekly_pnl.value}
            glow
          />
          <MetricCard
            icon={<Activity size={18} />}
            label="Realized P&L"
            value={money(pnlData.total_realized_pnl)}
            sub={`Unrealized: ${money(pnlData.total_unrealized_pnl)}`}
            trend={pnlData.total_realized_pnl}
            glow
          />
          <MetricCard
            icon={<Wallet size={18} />}
            label="Cash Available"
            value={money(portfolio.cash)}
            sub={`${holdings.length} holdings | ${percent(portfolio.total_value ? (portfolio.cash / portfolio.total_value) * 100 : 0)} liquid`}
          />
          <MetricCard
            icon={riskStatus.buying_halted ? <ShieldAlert size={18} /> : <Shield size={18} />}
            label="Risk Status"
            value={riskStatus.buying_halted ? "HALTED" : "ACTIVE"}
            sub={`Drawdown ${portfolio.drawdown_pct?.toFixed(1) || '0.0'}% / ${(riskStatus.drawdown_limit || 15).toFixed(0)}%`}
            trend={riskStatus.buying_halted ? -1 : 1}
          />
        </section>

        {/* Crisis Alert Banner */}
        {dashboard.news?.crisis_alerts?.length > 0 && (
          <div className="notice error" style={{display:'flex',gap:8,alignItems:'center'}}>
            <AlertTriangle size={18} />
            <strong>CRISIS ALERT:</strong>
            <span>{dashboard.news.crisis_alerts[0].reason || 'Crisis event detected in market news'}</span>
          </div>
        )}

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
                {["chart", "scanner", "trades", "ledger", "news"].map((tab) => (
                  <button
                    className={activeTab === tab ? "active" : ""}
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    type="button"
                  >
                    {tab === "news" ? "📰 News" : tab === "trades" ? "📊 Trades" : tab}
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
            {activeTab === "trades" && (
              <TradeHistoryPanel
                data={tradeHistory}
                page={tradeHistoryPage}
                onPageChange={setTradeHistoryPage}
              />
            )}
            {activeTab === "ledger" && <TradeLedger trades={dashboard.trades} />}
            {activeTab === "news" && <GlobalNewsPanel news={dashboard.news} />}
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
                label="Final Score"
                max={1}
                min={0}
                threshold={0.60}
                value={Number(selectedAnalysis?.final_score || 0)}
              />
              <ScoreGauge
                label="Gemini Confidence"
                max={1}
                min={0}
                threshold={0.60}
                value={Number(selectedAnalysis?.gemini_confidence || 0)}
              />
              <ScoreGauge
                label="ML Probability"
                max={1}
                min={0}
                threshold={0.55}
                value={Number(selectedAnalysis?.ml_confidence || 0)}
              />
              <ScoreGauge
                label="News Impact"
                max={1}
                min={-1}
                threshold={0.0}
                value={Number(selectedAnalysis?.gemini_sentiment_score || 0)}
                sentimentMode
              />
            </div>

            {selectedAnalysis?.crisis_detected && (
              <div className="matrix-cell sell" style={{marginBottom:8,padding:'8px 12px',display:'flex',gap:6,alignItems:'center'}}>
                <AlertTriangle size={16} />
                <strong>CRISIS MODE — trades restricted</strong>
              </div>
            )}

            <div className="pipeline">
              <PipelineStep label="Stage 1" value="Bulk screener (RSI+Vol)" />
              <PipelineStep label="Stage 2" value="News intelligence (3-level)" />
              <PipelineStep label="Stage 3" value="XGBoost + LightGBM ensemble" />
              <PipelineStep label="Stage 4" value="Gemini structured analyst" />
              <PipelineStep label="Stage 5" value="Risk manager" />
              <PipelineStep label="Final" value="Weighted execution matrix" />
            </div>

            <div className="matrix">
              <MatrixCell tone="buy" label="BUY" value={`Score ≥ 0.60 + No Crisis + Risk OK`} />
              <MatrixCell tone="sell" label="SELL" value={`Score < 0.30 OR Crisis OR Stop-Loss`} />
              <MatrixCell tone="hold" label="HOLD" value={`In between — wait for proof`} />
            </div>

            <div className="reason-box">
              <span>AI Decision Proof</span>
              <p>{selectedAnalysis?.action_reason || "No analysis decision has been logged for this ticker yet."}</p>
            </div>

            {selectedAnalysis?.gemini_risk_factors?.length > 0 && (
              <div className="reason-box" style={{borderLeft:'3px solid var(--sell)'}}>
                <span>⚠ Risk Factors</span>
                <ul style={{margin:'4px 0',paddingLeft:16,fontSize:'0.82rem',color:'var(--text-muted)'}}>
                  {selectedAnalysis.gemini_risk_factors.map((f, i) => <li key={i}>{f}</li>)}
                </ul>
              </div>
            )}

            <FeatureList features={selectedAnalysis?.ml_features_used || marketData?.indicators || {}} />
          </aside>
        </section>

        <section className="detail-grid">
          <NewsPanel analysis={selectedAnalysis} news={dashboard.news} ticker={selectedTicker} />
          <HoldingsPanel holdings={holdings} cash={portfolio.cash} riskStatus={portfolio.risk_status} sectorAllocation={portfolio.sector_allocation} stopPct={0.08} />
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

function MetricCard({ icon, label, value, sub, trend, glow }) {
  const glowClass = glow && trend !== undefined
    ? (Number(trend) >= 0 ? " pnl-positive" : " pnl-negative")
    : "";
  return (
    <article className={`metric${glowClass}`}>
      <div className="metric-label">
        {icon}
        <span>{label}</span>
      </div>
      <strong className={trend !== undefined ? signedClass(trend) : ""}>{value}</strong>
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

/* ══════════════════════════════════════════════════════════════════════
   PROFESSIONAL TRADE HISTORY — expandable rows, realized PnL, pagination
   ══════════════════════════════════════════════════════════════════════ */
function TradeHistoryPanel({ data, page, onPageChange }) {
  const [expandedRow, setExpandedRow] = useState(null);
  const trades = data?.trades || [];
  const totalPages = data?.total_pages || 1;

  return (
    <div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th></th>
              <th>Date</th>
              <th>Ticker</th>
              <th>Action</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Total</th>
              <th>Realized P&L</th>
              <th>Score</th>
            </tr>
          </thead>
          <tbody>
            {trades.length ? (
              trades.map((trade, idx) => (
                <TradeHistoryRow
                  key={`${trade.timestamp}-${trade.ticker}-${idx}`}
                  trade={trade}
                  isExpanded={expandedRow === idx}
                  onToggle={() => setExpandedRow(expandedRow === idx ? null : idx)}
                />
              ))
            ) : (
              <tr>
                <td colSpan="9" style={{textAlign:'center',padding:24,color:'var(--muted)'}}>
                  No trade history available. Run an analysis cycle to generate trades.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="pagination">
          <button
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            <ChevronLeft size={14} />
          </button>
          {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
            const pageNum = i + 1;
            return (
              <button
                key={pageNum}
                className={page === pageNum ? "active" : ""}
                onClick={() => onPageChange(pageNum)}
              >
                {pageNum}
              </button>
            );
          })}
          {totalPages > 7 && <span>...</span>}
          <button
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
          >
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

function TradeHistoryRow({ trade, isExpanded, onToggle }) {
  const hasPnl = trade.realized_pnl !== null && trade.realized_pnl !== undefined;
  return (
    <>
      <tr onClick={onToggle} style={{cursor:'pointer'}}>
        <td style={{width:28,padding:'10px 6px'}}>
          <ChevronDown
            size={14}
            style={{
              transition: 'transform 200ms ease',
              transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
              color: 'var(--muted)',
            }}
          />
        </td>
        <td>{dateTime(trade.timestamp)}</td>
        <td style={{fontWeight:700}}>{trade.ticker}</td>
        <td><ActionPill action={trade.action} /></td>
        <td>{Number(trade.quantity || 0).toLocaleString("en-IN")}</td>
        <td>{money(trade.price)}</td>
        <td>{money(trade.total_value)}</td>
        <td className={hasPnl ? signedClass(trade.realized_pnl) : ''}>
          {hasPnl ? (
            <>{money(trade.realized_pnl)} ({percent(trade.realized_pnl_pct)})</>
          ) : (
            <span style={{color:'var(--muted)'}}>—</span>
          )}
        </td>
        <td>{trade.final_score ? trade.final_score.toFixed(2) : '—'}</td>
      </tr>
      {isExpanded && (
        <tr className="trade-row-expanded">
          <td colSpan="9">
            <div className="trade-reasoning-label">AI Reasoning</div>
            <div>{trade.ai_reasoning || "No reasoning recorded for this trade."}</div>
            <div className="trade-reasoning-scores">
              <span>ML Conf: <strong>{probability(trade.ml_confidence)}</strong></span>
              <span>Gemini: <strong>{probability(trade.gemini_confidence)}</strong></span>
              <span>Score: <strong>{trade.final_score?.toFixed(3) || '—'}</strong></span>
              {trade.crisis_detected && <span style={{color:'var(--red)'}}>⚠ CRISIS</span>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function NewsPanel({ analysis, news, ticker }) {
  const headlines = analysis?.news_headlines || [];
  const macroNews = news?.macro_news || [];
  const tickerNewsData = news?.ticker_news?.find(t => t.ticker === ticker) || {};

  return (
    <article className="detail-panel">
      <div className="panel-title-row compact">
        <div>
          <h2>
            <Newspaper size={18} />
            News Intelligence
          </h2>
          <span>Multi-level: Macro → Sector → Stock</span>
        </div>
      </div>

      {/* Macro News */}
      {macroNews.length > 0 && (
        <div className="news-list">
          <div className="section-label" style={{display:'flex',gap:6,alignItems:'center'}}>
            <Globe size={14} /> Global / India Macro
          </div>
          {macroNews.slice(0, 4).map((item, i) => (
            <div className="news-item" key={`macro-${i}`}>
              <strong>
                <span style={{background:'var(--accent)',color:'var(--bg-card)',borderRadius:3,padding:'1px 5px',fontSize:'0.7rem'}}>MACRO</span>
                {typeof item === 'string' ? item : (item.headline || 'Untitled')}
              </strong>
              <small>{typeof item === 'object' ? item.source || 'News' : 'Google News'}</small>
            </div>
          ))}
        </div>
      )}

      {/* Stock Headlines */}
      <div className="news-list">
        <div className="section-label">📈 {ticker || 'Stock'} Headlines</div>
        {headlines.length ? (
          headlines.slice(0, 5).map((headline, index) => (
            <div className="news-item" key={`stock-${index}`}>
              <strong>
                <span>{index + 1}</span>
                {headline || "Untitled headline"}
              </strong>
              <small>Gemini analysis input</small>
            </div>
          ))
        ) : (
          <EmptyBlock title="No headlines" text="Run analysis to fetch news." />
        )}
      </div>

      <div className="reason-box analyst">
        <span>Gemini Structured Analysis</span>
        <p>{analysis?.gemini_explanation || "No LLM explanation has been logged yet."}</p>
      </div>
    </article>
  );
}

function HoldingsPanel({ holdings, cash, riskStatus, sectorAllocation, stopPct = 0.08 }) {
  return (
    <article className="detail-panel">
      <div className="panel-title-row compact">
        <div>
          <h2>
            <Wallet size={18} />
            Portfolio Holdings
          </h2>
          <span>Live-priced positions with trailing stop protection</span>
        </div>
      </div>

      {/* Risk Status Bar */}
      {riskStatus && (
        <div style={{padding:'8px 12px',fontSize:'0.75rem',display:'flex',gap:12,flexWrap:'wrap',borderBottom:'1px solid var(--border)',color:'var(--muted)'}}>
          <span>Stop-Loss: <strong style={{color:'var(--text)'}}>{riskStatus.stop_loss_pct?.toFixed(0) || 7}%</strong></span>
          <span>Trailing: <strong style={{color:'var(--text)'}}>{(stopPct * 100).toFixed(0)}%</strong></span>
          <span>Max/Sector: <strong style={{color:'var(--text)'}}>{riskStatus.max_sector_stocks || 3}</strong></span>
          <span>Drawdown: <strong className={riskStatus.buying_halted ? 'negative' : ''}>{riskStatus.drawdown_pct?.toFixed(1) || '0.0'}%</strong> / {riskStatus.drawdown_limit?.toFixed(0) || 15}%</span>
          {riskStatus.buying_halted && <span className="action-pill sell" style={{fontSize:'0.65rem'}}>BUYING HALTED</span>}
        </div>
      )}

      {/* Sector Allocation */}
      {sectorAllocation && Object.keys(sectorAllocation).length > 0 && (
        <div style={{padding:'6px 12px',fontSize:'0.72rem',display:'flex',gap:6,flexWrap:'wrap',borderBottom:'1px solid var(--border)'}}>
          {Object.entries(sectorAllocation).map(([sector, value]) => (
            <span key={sector} style={{background:'var(--panel-2)',padding:'2px 8px',borderRadius:4}}>
              {sector}: <strong>{money(value)}</strong>
            </span>
          ))}
        </div>
      )}

      <div className="holdings-list">
        {holdings.length ? (
          holdings.map((holding) => {
            // Calculate trailing stop distance
            const peakPrice = holding.peak_price || holding.avg_price || 0;
            const currentPrice = holding.current_price || holding.avg_price || 0;
            const trailingStopPrice = peakPrice * (1 - stopPct);
            const distancePct = currentPrice > 0 ? ((currentPrice - trailingStopPrice) / currentPrice) * 100 : 0;
            const distanceClamped = Math.max(0, Math.min(100, distancePct));
            const stopZone = distanceClamped > 5 ? 'safe' : distanceClamped > 2 ? 'warning' : 'danger';

            return (
              <div className="holding-row" key={holding.ticker} style={{gridTemplateColumns:'1fr'}}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',gap:12}}>
                  <div>
                    <strong>{holding.ticker}</strong>
                    <small>
                      {holding.quantity} shares at avg {money(holding.avg_price)}
                      {holding.sector && <span style={{marginLeft:6,opacity:0.6}}>• {holding.sector}</span>}
                    </small>
                  </div>
                  <div className="holding-values">
                    <strong className={signedClass(holding.unrealized_pnl)}>{money(holding.market_value)}</strong>
                    <small className={signedClass(holding.unrealized_pnl)}>
                      {money(holding.unrealized_pnl)} ({percent(holding.unrealized_pnl_pct)})
                    </small>
                  </div>
                </div>

                {/* Trailing Stop Distance Indicator */}
                {peakPrice > 0 && (
                  <div className="stop-distance">
                    <Target size={12} style={{color:'var(--muted)',flexShrink:0}} />
                    <div className="stop-bar-track">
                      <div
                        className={`stop-bar-fill ${stopZone}`}
                        style={{width: `${Math.min(100, distanceClamped * 10)}%`}}
                      />
                    </div>
                    <span className={`stop-label ${stopZone}`}>
                      {distancePct.toFixed(1)}% to stop
                    </span>
                  </div>
                )}
              </div>
            );
          })
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

function GlobalNewsPanel({ news }) {
  if (!news) return <EmptyBlock title="News not loaded" text="Run an analysis cycle to fetch global news intelligence." />;

  const macroNews = news.macro_news || [];
  const tickerNews = news.ticker_news || [];
  const crisisAlerts = news.crisis_alerts || [];

  return (
    <div style={{padding:16,overflow:'auto',maxHeight:500}}>
      {crisisAlerts.length > 0 && (
        <div className="matrix-cell sell" style={{marginBottom:12,padding:'10px 14px'}}>
          <span style={{display:'flex',gap:6,alignItems:'center'}}><AlertTriangle size={16} /> Crisis Alerts</span>
          {crisisAlerts.map((a, i) => (
            <strong key={i} style={{display:'block',marginTop:4}}>{a.ticker}: {a.reason}</strong>
          ))}
        </div>
      )}

      <div className="section-label" style={{marginBottom:8,display:'flex',gap:6,alignItems:'center'}}>
        <Globe size={14} /> Global & India Macro News ({macroNews.length})
      </div>
      {macroNews.slice(0, 10).map((item, i) => (
        <div className="news-item" key={`gnews-${i}`} style={{marginBottom:6}}>
          <strong style={{fontSize:'0.82rem'}}>{typeof item === 'string' ? item : item.headline}</strong>
          <small style={{opacity:0.5}}>{typeof item === 'object' ? item.source : ''}</small>
        </div>
      ))}

      {tickerNews.length > 0 && (
        <>
          <div className="section-label" style={{marginTop:16,marginBottom:8}}>📊 Per-Ticker News Scores</div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(200px,1fr))',gap:8}}>
            {tickerNews.slice(0, 12).map((t, i) => (
              <div key={i} style={{background:'var(--bg-hover)',padding:'8px 10px',borderRadius:6,fontSize:'0.8rem'}}>
                <strong>{t.ticker}</strong>
                <span style={{marginLeft:6,opacity:0.6}}>{t.sector}</span>
                <div style={{marginTop:4}}>
                  Score: <strong className={t.overall_news_score >= 0 ? 'positive' : 'negative'}>
                    {t.overall_news_score?.toFixed(3) || '0.000'}
                  </strong>
                  {t.crisis_detected && <span className="action-pill sell" style={{marginLeft:6,fontSize:'0.65rem'}}>CRISIS</span>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
