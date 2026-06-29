import { useEffect, useRef, useMemo } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CircleDollarSign,
  Shield,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  Wallet,
  Globe,
} from "lucide-react";
import { gsap } from "gsap";
import { createChart } from "lightweight-charts";
import { useApp } from "../context/AppContext";
import { useNavigate } from "react-router-dom";
import Topbar from "../components/Topbar";
import MetricCard from "../components/MetricCard";
import ActionPill from "../components/ActionPill";
import EmptyState from "../components/EmptyState";
import PriceTicker from "../components/PriceTicker";
import { money, percent, dateTime, compactMoney, signedClass } from "../format";

export default function DashboardPage() {
  const { dashboard, pnlData, status, watchlistRows, loadTicker, realtime, selectedTicker, selectedAnalysis } = useApp();
  const navigate = useNavigate();
  const portfolio = dashboard.portfolio || {};
  const holdings = portfolio.holdings || [];
  const riskStatus = portfolio.risk_status || {};
  const chartRef = useRef(null);
  const chartInstance = useRef(null);
  const contentRef = useRef(null);

  const monthlyPnl = useMemo(() => {
    const currentValue = pnlData.total_portfolio_value || portfolio.total_value || 1000000;
    const history = dashboard.portfolioHistory || [];
    if (history.length === 0) {
      return { value: 0, pct: 0 };
    }
    
    const targetDate = new Date();
    targetDate.setDate(targetDate.getDate() - 30);
    
    let closestSnapshot = null;
    let minDiff = Infinity;
    
    for (const snap of history) {
      if (!snap.timestamp) continue;
      const snapDate = new Date(snap.timestamp);
      if (snapDate <= targetDate) {
        const diff = targetDate - snapDate;
        if (diff < minDiff) {
          minDiff = diff;
          closestSnapshot = snap;
        }
      }
    }
    
    const baseValue = closestSnapshot 
      ? closestSnapshot.total_value 
      : (history[0]?.total_value || portfolio.initial_balance || 1000000);
      
    const value = currentValue - baseValue;
    const pct = baseValue > 0 ? (value / baseValue) * 100 : 0;
    return { value, pct };
  }, [pnlData.total_portfolio_value, portfolio.total_value, portfolio.initial_balance, dashboard.portfolioHistory]);

  const news = dashboard.news || {};
  const macroNews = news.macro_news || [];
  const tickerNews = news.ticker_news || [];
  
  const topNewsScores = useMemo(() => {
    const hasScores = tickerNews.some(t => (t.overall_news_score || 0) !== 0);
    if (!hasScores && watchlistRows.length > 0) {
      return [...watchlistRows]
        .map(r => ({
          ticker: r.ticker,
          overall_news_score: r.sentiment,
          sector: dashboard.analyses.find(a => a.ticker === r.ticker)?.sector || "Sector",
          crisis_detected: dashboard.analyses.find(a => a.ticker === r.ticker)?.crisis_detected || false,
        }))
        .sort((a, b) => (b.overall_news_score || 0) - (a.overall_news_score || 0));
    }
    return [...tickerNews].sort((a, b) => (b.overall_news_score || 0) - (a.overall_news_score || 0));
  }, [tickerNews, watchlistRows, dashboard.analyses]);

  // Equity curve chart
  useEffect(() => {
    const el = chartRef.current;
    if (!el || dashboard.portfolioHistory.length === 0) return;

    if (chartInstance.current) {
      chartInstance.current.remove();
      chartInstance.current = null;
    }

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#787b86",
        fontFamily: "Inter, sans-serif",
      },
      grid: {
        vertLines: { color: "rgba(39, 39, 42, 0.5)" },
        horzLines: { color: "rgba(39, 39, 42, 0.5)" },
      },
      rightPriceScale: { borderColor: "#27272a" },
      timeScale: { borderColor: "#27272a", timeVisible: true },
      crosshair: {
        vertLine: { color: "rgba(161, 161, 170, 0.4)", labelBackgroundColor: "#27272a" },
        horzLine: { color: "rgba(161, 161, 170, 0.4)", labelBackgroundColor: "#27272a" },
      },
      localization: { priceFormatter: (p) => compactMoney(p) },
    });

    const areaSeries = chart.addAreaSeries({
      topColor: "rgba(34, 197, 94, 0.2)",
      bottomColor: "rgba(34, 197, 94, 0.01)",
      lineColor: "#22c55e",
      lineWidth: 2,
    });

    const data = dashboard.portfolioHistory
      .filter((s) => s.timestamp && s.total_value)
      .map((s) => {
        const d = new Date(s.timestamp);
        return { time: Math.floor(d.getTime() / 1000), value: s.total_value };
      })
      .sort((a, b) => a.time - b.time);

    // Deduplicate by time
    const seen = new Set();
    const deduped = data.filter((d) => {
      if (seen.has(d.time)) return false;
      seen.add(d.time);
      return true;
    });

    if (deduped.length > 0) {
      areaSeries.setData(deduped);
      chart.timeScale().fitContent();
    }

    chartInstance.current = chart;
    return () => {
      chart.remove();
      chartInstance.current = null;
    };
  }, [dashboard.portfolioHistory]);

  // GSAP entrance
  useEffect(() => {
    if (contentRef.current) {
      gsap.fromTo(
        contentRef.current.children,
        { opacity: 0, y: 25 },
        {
          opacity: 1,
          y: 0,
          stagger: 0.08,
          duration: 0.6,
          ease: "power3.out",
        }
      );
    }
  }, [status.loading]);

  if (status.loading) {
    return (
      <>
        <Topbar title="Dashboard" subtitle="Loading..." />
        <div>
          <div className="metrics-grid">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="metric">
                <div className="skeleton" style={{ width: 100, height: 14 }} />
                <div className="skeleton" style={{ width: 140, height: 28, marginTop: 8 }} />
                <div className="skeleton" style={{ width: 120, height: 12, marginTop: 6 }} />
              </div>
            ))}
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar title="Dashboard" subtitle="Portfolio overview & market intelligence" />
      <PriceTicker prices={realtime.prices} holdings={holdings} />
      <div ref={contentRef}>
        {/* Status Notice */}
        {(status.error || status.message) && (
          <div className={`notice ${status.error ? "error" : "success"}`} style={{ marginBottom: "16px" }}>
            <span>{status.error || status.message}</span>
          </div>
        )}

        {/* Crisis Alert */}
        {dashboard.news?.crisis_alerts?.length > 0 && (
          <div className="notice error" style={{ marginBottom: "16px" }}>
            <AlertTriangle size={16} />
            <strong>CRISIS ALERT:</strong>
            <span>{dashboard.news.crisis_alerts[0].reason || "Crisis event detected"}</span>
          </div>
        )}

        {/* KPI Metrics */}
        <section className="metrics-grid" style={{ 
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', 
          gap: '12px',
          marginBottom: "20px" 
        }}>
          <MetricCard
            icon={<CircleDollarSign size={18} />}
            label="Portfolio Value"
            value={money(pnlData.total_portfolio_value || portfolio.total_value)}
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
            icon={monthlyPnl.value >= 0 ? <ArrowUpRight size={18} /> : <ArrowDownRight size={18} />}
            label="Monthly P&L"
            value={money(monthlyPnl.value)}
            sub={percent(monthlyPnl.pct)}
            trend={monthlyPnl.value}
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
            sub={`Invested: ${money(pnlData.invested_capital)}`}
          />
          <MetricCard
            icon={<CircleDollarSign size={18} />}
            label="Invested Capital"
            value={money(pnlData.invested_capital)}
            sub={`Charges: ${money(pnlData.total_charges_paid)}`}
          />
          <MetricCard
            icon={riskStatus.buying_halted ? <ShieldAlert size={18} /> : <Shield size={18} />}
            label="Risk Status"
            value={riskStatus.buying_halted ? "HALTED" : "ACTIVE"}
            sub={`Drawdown ${portfolio.drawdown_pct?.toFixed(1) || "0.0"}% / ${(
              riskStatus.drawdown_limit || 15
            ).toFixed(0)}%`}
            trend={riskStatus.buying_halted ? -1 : 1}
          />
        </section>

        {/* Dashboard Grid */}
        <div className="dashboard-grid">
          {/* Equity Curve */}
          <div className="detail-panel" style={{ gridColumn: "1 / -1" }}>
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <TrendingUp size={16} />
                  Portfolio Equity Curve
                </h2>
                <span>Historical portfolio value over time</span>
              </div>
            </div>
            <div style={{ height: 320, position: "relative", marginTop: "12px" }}>
              <div ref={chartRef} style={{ position: "absolute", inset: 0 }} />
              {dashboard.portfolioHistory.length === 0 && (
                <EmptyState
                  title="No history yet"
                  text="Run analysis cycles to build portfolio history."
                />
              )}
            </div>
          </div>

          {/* Recent Trades */}
          <div className="detail-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <Activity size={16} />
                  Recent Trades
                </h2>
                <span>Latest BUY/SELL executions</span>
              </div>
            </div>
            <div className="table-scroll" style={{ maxHeight: 300, marginTop: "12px" }}>
              {dashboard.trades.length > 0 ? (
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Ticker</th>
                      <th>Action</th>
                      <th>Price</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.trades.slice(0, 8).map((trade, i) => (
                      <tr key={`${trade.timestamp}-${i}`}>
                        <td>{dateTime(trade.timestamp)}</td>
                        <td
                          style={{ cursor: "pointer", color: "var(--accent)" }}
                          onClick={() => {
                            loadTicker(trade.ticker);
                            navigate("/");
                          }}
                          title={`Inspect ${trade.ticker}`}
                        >
                          <strong>{trade.ticker}</strong>
                        </td>
                        <td><ActionPill action={trade.action} /></td>
                        <td>{money(trade.price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState title="No trades" text="Run analysis to generate trades." />
              )}
            </div>
          </div>

          {/* Top Movers */}
          <div className="detail-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <TrendingUp size={16} />
                  Top Watchlist
                </h2>
                <span>Highest ML confidence scores</span>
              </div>
            </div>
            <div className="watchlist" style={{ maxHeight: 300, overflowY: "auto", marginTop: "12px" }}>
              {watchlistRows.slice(0, 8).map((row) => (
                <div
                  key={row.ticker}
                  className="watchlist-item"
                  style={{ cursor: "pointer" }}
                  onClick={() => {
                    loadTicker(row.ticker);
                    navigate("/");
                  }}
                  title={`Inspect ${row.ticker}`}
                >
                  <div>
                    <strong>{row.ticker}</strong>
                    <small>ML {(row.ml * 100).toFixed(0)}% • Sent {row.sentiment >= 0 ? "+" : ""}{row.sentiment.toFixed(2)}</small>
                  </div>
                  <div className="watchlist-right">
                    <ActionPill action={row.action} />
                    <span>{row.price ? money(row.price) : "--"}</span>
                  </div>
                </div>
              ))}
              {watchlistRows.length === 0 && (
                <EmptyState title="No data" text="Connect backend to load watchlist." />
              )}
            </div>
          </div>
        </div>

        {/* News Intelligence Section */}
        <div className="section-label" style={{ marginTop: "24px", marginBottom: "12px", fontSize: "14px", fontWeight: "700" }}>
          News Intelligence & Sentiment
        </div>
        <div className="detail-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '16px', marginBottom: "20px" }}>
          {/* Global & India Macro Headlines */}
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
            <div className="news-list" style={{ maxHeight: 350, overflowY: "auto", marginTop: "12px" }}>
              {macroNews.length > 0 ? (
                macroNews.slice(0, 10).map((item, i) => (
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
                <EmptyState title="No macro news" text="No global news headlines loaded." />
              )}
            </div>
          </div>

          {/* Stock Specific Headlines Column */}
          <div className="detail-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <Globe size={16} style={{ color: 'var(--accent)' }} />
                  {selectedTicker || "Stock"} Headlines
                </h2>
                <span>Ticker-specific headlines used by strategy</span>
              </div>
            </div>
            <div className="news-list" style={{ maxHeight: 350, overflowY: "auto", marginTop: "12px" }}>
              {selectedAnalysis?.news_headlines && selectedAnalysis.news_headlines.length > 0 ? (
                selectedAnalysis.news_headlines.slice(0, 10).map((h, i) => (
                  <div className="news-item" key={`stock-headline-${i}`}>
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
                      {h}
                    </strong>
                    <small>LLM analysis input</small>
                  </div>
                ))
              ) : (
                <EmptyState
                  title="No headlines"
                  text={selectedTicker ? `No headlines found for ${selectedTicker}.` : "Select a ticker to see headlines."}
                />
              )}
            </div>
          </div>

          {/* Top News Sentiment Scores */}
          <div className="detail-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <TrendingUp size={16} />
                  Top News Sentiment Scores ({topNewsScores.length})
                </h2>
                <span>Tickers sorted by highest overall news score</span>
              </div>
            </div>
            <div className="news-list" style={{ maxHeight: 350, overflowY: "auto", marginTop: "12px" }}>
              {topNewsScores.length > 0 ? (
                topNewsScores.slice(0, 10).map((t, i) => (
                  <div
                    className="news-item"
                    key={`news-score-${i}`}
                    style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
                    onClick={() => {
                      loadTicker(t.ticker);
                      navigate("/");
                    }}
                    title={`Inspect ${t.ticker}`}
                  >
                    <div>
                      <strong style={{ fontSize: '13px' }}>{t.ticker}</strong>
                      <small style={{ color: 'var(--muted)', fontSize: '10.5px', display: 'block', marginTop: '2px' }}>{t.sector}</small>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', textAlign: 'right' }}>
                      <strong className={signedClass(t.overall_news_score)} style={{ fontFamily: "JetBrains Mono, monospace" }}>
                        {t.overall_news_score >= 0 ? "+" : ""}{t.overall_news_score?.toFixed(3) || "0.000"}
                      </strong>
                      {t.crisis_detected && <span className="action-pill sell" style={{ fontSize: "8px", padding: '1px 3px' }}>CRISIS</span>}
                    </div>
                  </div>
                ))
              ) : (
                <EmptyState title="No scores" text="No news sentiment scores loaded." />
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
