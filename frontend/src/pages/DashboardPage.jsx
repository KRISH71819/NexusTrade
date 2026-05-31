import { useEffect, useRef } from "react";
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
} from "lucide-react";
import { gsap } from "gsap";
import { createChart } from "lightweight-charts";
import { useApp } from "../context/AppContext";
import Topbar from "../components/Topbar";
import MetricCard from "../components/MetricCard";
import ActionPill from "../components/ActionPill";
import EmptyState from "../components/EmptyState";
import { money, percent, dateTime, compactMoney } from "../format";

export default function DashboardPage() {
  const { dashboard, pnlData, status, watchlistRows, loadTicker } = useApp();
  const portfolio = dashboard.portfolio || {};
  const holdings = portfolio.holdings || [];
  const riskStatus = portfolio.risk_status || {};
  const chartRef = useRef(null);
  const chartInstance = useRef(null);
  const contentRef = useRef(null);

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
        vertLines: { color: "rgba(42, 46, 57, 0.5)" },
        horzLines: { color: "rgba(42, 46, 57, 0.5)" },
      },
      rightPriceScale: { borderColor: "#2a2e39" },
      timeScale: { borderColor: "#2a2e39", timeVisible: true },
      crosshair: {
        vertLine: { color: "rgba(41, 98, 255, 0.5)", labelBackgroundColor: "#2962ff" },
        horzLine: { color: "rgba(41, 98, 255, 0.5)", labelBackgroundColor: "#2962ff" },
      },
      localization: { priceFormatter: (p) => compactMoney(p) },
    });

    const areaSeries = chart.addAreaSeries({
      topColor: "rgba(41, 98, 255, 0.25)",
      bottomColor: "rgba(41, 98, 255, 0.02)",
      lineColor: "#2962ff",
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
        <section className="metrics-grid" style={{ marginBottom: "20px" }}>
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
            sub={`${holdings.length} holdings • ${percent(
              portfolio.total_value ? (portfolio.cash / portfolio.total_value) * 100 : 0
            )} liquid`}
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
                        <td><strong>{trade.ticker}</strong></td>
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
                  style={{ cursor: "default" }}
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
      </div>
    </>
  );
}
