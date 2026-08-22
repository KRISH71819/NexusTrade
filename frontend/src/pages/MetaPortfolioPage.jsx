import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CalendarClock,
  CircleDollarSign,
  Gauge,
  Layers,
  Shield,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { gsap } from "gsap";
import { createChart } from "lightweight-charts";
import Topbar from "../components/Topbar";
import MetricCard from "../components/MetricCard";
import { api } from "../api";
import { money, percent, dateTime, compactMoney, signedClass } from "../format";

export default function MetaPortfolioPage() {
  const [summary, setSummary] = useState(null);
  const [statusDoc, setStatusDoc] = useState(null);
  const [equityDocs, setEquityDocs] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const chartRef = useRef(null);
  const chartInstance = useRef(null);
  const contentRef = useRef(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [sum, st, eq] = await Promise.all([
          api.metaSummary(),
          api.metaStatus(),
          api.metaEquity(),
        ]);
        if (!alive) return;
        setSummary(sum);
        setStatusDoc(st);
        setEquityDocs(eq?.equity || []);
        setError(sum?.status === "no_portfolio" ? "" : "");
      } catch (e) {
        if (alive) setError(e.message || String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 60000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // GSAP entrance
  useEffect(() => {
    if (contentRef.current && !loading) {
      gsap.fromTo(
        contentRef.current.children,
        { opacity: 0, y: 22 },
        { opacity: 1, y: 0, stagger: 0.07, duration: 0.55, ease: "power3.out" }
      );
    }
  }, [loading, summary]);

  // Equity curve chart
  useEffect(() => {
    if (!chartRef.current) return;

    const chart = createChart(chartRef.current, {
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

    const data = equityDocs
      .filter((s) => s.total_value)
      .map((s) => {
        const d = s.timestamp ? new Date(s.timestamp) : new Date(s.date);
        return { time: Math.floor(d.getTime() / 1000), value: s.total_value };
      })
      .sort((a, b) => a.time - b.time);

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
  }, [equityDocs]);

  if (loading) {
    return (
      <>
        <Topbar title="System B" subtitle="Loading meta portfolio..." />
        <div className="metrics-grid">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="metric">
              <div className="skeleton" style={{ width: 100, height: 14 }} />
              <div className="skeleton" style={{ width: 140, height: 28, marginTop: 8 }} />
              <div className="skeleton" style={{ width: 120, height: 12, marginTop: 6 }} />
            </div>
          ))}
        </div>
      </>
    );
  }

  const noBook = summary?.status === "no_portfolio";
  const holdings = statusDoc?.portfolio?.holdings || [];
  const trades = statusDoc?.recent_trades || [];
  const killOn = !!summary?.kill_switch_active;
  const inceptionPct = summary ? summary.since_inception_pct * 100 : 0;
  const dailyPct = summary ? summary.daily_pnl_pct * 100 : 0;
  const weeklyPct = summary ? summary.weekly_pnl_pct * 100 : 0;

  return (
    <>
      <Topbar
        title="System B"
        subtitle="Validated quant strategy — trend momentum top-25 | isolated paper book"
      />

      {/* Config badge + kill-switch pill */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          flexWrap: "wrap",
          marginBottom: "14px",
        }}
      >
        <span
          style={{
            fontSize: "11px",
            fontWeight: 700,
            letterSpacing: "0.3px",
            color: "#93c5fd",
            background: "rgba(59, 130, 246, 0.1)",
            border: "1px solid rgba(59, 130, 246, 0.35)",
            padding: "5px 10px",
            borderRadius: "6px",
            fontFamily: "JetBrains Mono, monospace",
          }}
        >
          {summary?.badge ||
            "SYSTEM B — VALIDATED CONFIG: trend_200 rank | top-25 | 60d rebalance | trend overlay | vol target 15%"}
        </span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "6px",
            fontSize: "11px",
            fontWeight: 700,
            color: killOn ? "#fca5a5" : "#86efac",
            background: killOn ? "rgba(239, 68, 68, 0.12)" : "rgba(34, 197, 94, 0.1)",
            border: `1px solid ${killOn ? "rgba(239, 68, 68, 0.4)" : "rgba(34, 197, 94, 0.35)"}`,
            padding: "5px 10px",
            borderRadius: "6px",
          }}
        >
          {killOn ? <ShieldAlert size={13} /> : <Shield size={13} />}
          KILL SWITCH {killOn ? "ON ⛔" : "OFF"}
        </span>
      </div>

      {error && (
        <div className="notice error" style={{ marginBottom: "16px" }}>
          <span>{error}</span>
        </div>
      )}

      {noBook ? (
        <div className="notice" style={{ marginBottom: "16px" }}>
          <Activity size={16} />
          <span>
            Meta book is not seeded yet. It seeds itself at the first scheduled
            rebalance (15:40 IST) or via POST /api/meta/rebalance.
          </span>
        </div>
      ) : (
        <div ref={contentRef}>
          {/* KPI Metrics */}
          <section
            className="metrics-grid"
            style={{
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "12px",
              marginBottom: "20px",
            }}
          >
            <MetricCard
              icon={<CircleDollarSign size={18} />}
              label="Portfolio Value"
              value={money(summary.total_value)}
              sub={`${inceptionPct >= 0 ? "+" : ""}${inceptionPct.toFixed(2)}% since inception`}
              trend={summary.since_inception_pct}
              glow
            />
            <MetricCard
              icon={summary.daily_pnl >= 0 ? <TrendingUp size={18} /> : <TrendingDown size={18} />}
              label="Daily P&L"
              value={money(summary.daily_pnl)}
              sub={percent(dailyPct)}
              trend={summary.daily_pnl}
              glow
            />
            <MetricCard
              icon={summary.weekly_pnl >= 0 ? <ArrowUpRight size={18} /> : <ArrowDownRight size={18} />}
              label="Weekly P&L"
              value={money(summary.weekly_pnl)}
              sub={percent(weeklyPct)}
              trend={summary.weekly_pnl}
              glow
            />
            <MetricCard
              icon={<Activity size={18} />}
              label="Realized P&L"
              value={money(summary.realized_pnl)}
              sub="net of charges"
              trend={summary.realized_pnl}
              glow
            />
            <MetricCard
              icon={<Activity size={18} />}
              label="Unrealized P&L"
              value={money(summary.unrealized_pnl)}
              sub={`${holdings.length} open position(s)`}
              trend={summary.unrealized_pnl}
              glow
            />
            <MetricCard
              icon={<Wallet size={18} />}
              label="Cash"
              value={money(summary.cash)}
              sub={`Invested: ${money(summary.holdings_value)}`}
            />
            <MetricCard
              icon={<Gauge size={18} />}
              label="Exposure"
              value={
                summary.exposure_actual != null
                  ? `${(summary.exposure_actual * 100).toFixed(0)}%`
                  : "--"
              }
              sub={`target ${
                summary.exposure_target != null
                  ? `${(summary.exposure_target * 100).toFixed(0)}%`
                  : "--"
              }`}
            />
            <MetricCard
              icon={<Gauge size={18} />}
              label="Vol Scale"
              value={
                summary.vol_scale != null ? `x${summary.vol_scale}` : "--"
              }
              sub={
                summary.trend_on === false
                  ? "trend overlay OFF — defensive"
                  : `realized vol ${((summary.realized_vol || 0) * 100).toFixed(0)}%`
              }
            />
            <MetricCard
              icon={<Layers size={18} />}
              label="Holdings"
              value={String(summary.holdings_count ?? holdings.length)}
              sub={
                summary.names_target != null
                  ? `target ${summary.names_target}`
                  : "target 25"
              }
            />
            <MetricCard
              icon={<CalendarClock size={18} />}
              label="Next Rebalance"
              value={summary.next_rebalance_date || "--"}
              sub={
                summary.days_until_rebalance != null
                  ? `in ${summary.days_until_rebalance} day(s)`
                  : "awaiting first rebalance"
              }
            />
          </section>

          {/* Chart + recent trades */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 2fr) minmax(260px, 1fr)",
              gap: "14px",
              marginBottom: "20px",
            }}
          >
            <div
              style={{
                background: "var(--panel, rgba(255,255,255,0.02))",
                border: "1px solid rgba(39, 39, 42, 0.8)",
                borderRadius: "10px",
                padding: "12px",
                height: "340px",
              }}
            >
              <div
                style={{
                  fontSize: "12px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "6px",
                }}
              >
                EQUITY CURVE — META BOOK
              </div>
              <div ref={chartRef} style={{ height: "280px" }} />
            </div>

            <div
              style={{
                background: "var(--panel, rgba(255,255,255,0.02))",
                border: "1px solid rgba(39, 39, 42, 0.8)",
                borderRadius: "10px",
                padding: "12px",
                overflowY: "auto",
                maxHeight: "340px",
              }}
            >
              <div
                style={{
                  fontSize: "12px",
                  fontWeight: 700,
                  color: "var(--text-secondary)",
                  marginBottom: "8px",
                }}
              >
                RECENT TRADES
              </div>
              {trades.length === 0 && (
                <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                  No trades yet.
                </div>
              )}
              {(trades || []).map((t, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "6px 0",
                    borderBottom: "1px solid rgba(39, 39, 42, 0.6)",
                    gap: "8px",
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                      <strong style={{ fontSize: "12px" }}>{t.ticker}</strong>
                      <span
                        style={{
                          fontSize: "10px",
                          fontWeight: 700,
                          padding: "1px 6px",
                          borderRadius: "4px",
                          color: t.action === "BUY" ? "#86efac" : "#fca5a5",
                          background:
                            t.action === "BUY"
                              ? "rgba(34, 197, 94, 0.12)"
                              : "rgba(239, 68, 68, 0.12)",
                        }}
                      >
                        {t.action}
                      </span>
                    </div>
                    <small
                      style={{
                        fontSize: "10px",
                        color: "var(--text-secondary)",
                        display: "block",
                      }}
                    >
                      {dateTime(t.timestamp)}
                    </small>
                  </div>
                  <div style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <div style={{ fontSize: "12px", fontWeight: 700 }}>
                      {t.quantity} × {money(t.price)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Holdings table */}
          <div
            style={{
              background: "var(--panel, rgba(255,255,255,0.02))",
              border: "1px solid rgba(39, 39, 42, 0.8)",
              borderRadius: "10px",
              padding: "12px",
              overflowX: "auto",
            }}
          >
            <div
              style={{
                fontSize: "12px",
                fontWeight: 700,
                color: "var(--text-secondary)",
                marginBottom: "8px",
              }}
            >
              HOLDINGS ({holdings.length})
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
              <thead>
                <tr style={{ color: "var(--text-secondary)", textAlign: "left" }}>
                  <th style={{ padding: "6px 8px" }}>Ticker</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>Qty</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>Avg Price</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>LTP</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>Value</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>P&L</th>
                </tr>
              </thead>
              <tbody>
                {holdings.length === 0 && (
                  <tr>
                    <td colSpan={6} style={{ padding: "10px 8px", color: "var(--text-secondary)" }}>
                      No holdings yet.
                    </td>
                  </tr>
                )}
                {holdings.map((h) => {
                  const pnl =
                    ((h.current_price || h.avg_price || 0) - (h.avg_price || 0)) *
                    (h.quantity || 0);
                  return (
                    <tr key={h.ticker} style={{ borderTop: "1px solid rgba(39, 39, 42, 0.6)" }}>
                      <td style={{ padding: "6px 8px", fontWeight: 700 }}>{h.ticker}</td>
                      <td style={{ padding: "6px 8px", textAlign: "right" }}>{h.quantity}</td>
                      <td style={{ padding: "6px 8px", textAlign: "right" }}>
                        {money(h.avg_price)}
                      </td>
                      <td style={{ padding: "6px 8px", textAlign: "right" }}>
                        {h.current_price ? money(h.current_price) : "--"}
                      </td>
                      <td style={{ padding: "6px 8px", textAlign: "right" }}>
                        {money(h.market_value ?? (h.quantity || 0) * (h.avg_price || 0))}
                      </td>
                      <td
                        className={signedClass(pnl)}
                        style={{ padding: "6px 8px", textAlign: "right" }}
                      >
                        {money(pnl)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
