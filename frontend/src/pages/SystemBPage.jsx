import { useState, useEffect, useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api";
import { money, percent } from "../format";
import StatusBanner from "../components/v2/StatusBanner";
import MetricCard from "../components/v2/MetricCard";
import HoldingsTable from "../components/v2/HoldingsTable";
import EquityCurve from "../components/v2/EquityCurve";

export default function SystemBPage() {
  const [data, setData] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadData = useCallback(async () => {
    try {
      const [summary, hist] = await Promise.all([
        api.metaSummary().catch((e) => ({ db_online: false, error: e.message })),
        api.portfolioHistory(120).catch(() => ({ history: [] })),
      ]);
      setData(summary);
      setHistory(hist?.history || []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 60000); // 60s low-overhead polling
    return () => clearInterval(interval);
  }, [loadData]);

  if (loading && !data) {
    return (
      <div className="v2-page-loading">
        <RefreshCw className="v2-spin" size={24} />
        <span>Loading Portfolio Cockpit...</span>
      </div>
    );
  }

  const portfolio = data?.portfolio || data || {};
  const telemetry = {
    db_online: data?.db_online ?? true,
    kill_switch_active: data?.kill_switch_active ?? false,
  };

  const totalValue = portfolio.total_value ?? portfolio.total_equity ?? 100000;
  const cash = portfolio.cash ?? 0;
  const unrealizedPnl = portfolio.unrealized_pnl ?? 0;
  const realizedPnl = portfolio.realized_pnl ?? 0;
  const regime = data?.regime || data?.market_regime || "NEUTRAL";
  const exposure = data?.target_exposure != null ? `${(data.target_exposure * 100).toFixed(0)}%` : "80%";

  return (
    <div className="v2-page">
      <header className="v2-header">
        <div>
          <h2>Quantitative Portfolio Cockpit</h2>
          <p className="v2-subtitle">End-of-Day (EOD) trend momentum & multi-sleeve allocator</p>
        </div>
        <button className="v2-btn v2-btn-secondary" onClick={loadData} title="Refresh">
          <RefreshCw size={14} /> Refresh
        </button>
      </header>

      <StatusBanner telemetry={telemetry} />

      {error && <div className="v2-banner v2-banner-danger">{error}</div>}

      <div className="v2-metric-grid">
        <MetricCard label="Total Equity" value={money(totalValue)} sub={`Cash: ${money(cash)}`} />
        <MetricCard
          label="Unrealized P&L"
          value={money(unrealizedPnl)}
          trend={unrealizedPnl}
          sub={portfolio.initial_balance ? percent((unrealizedPnl / portfolio.initial_balance) * 100) : ""}
        />
        <MetricCard
          label="Realized P&L"
          value={money(realizedPnl)}
          trend={realizedPnl}
          sub="Net after charges"
        />
        <MetricCard
          label="Regime & Target Exposure"
          value={regime}
          sub={`Target Exposure: ${exposure}`}
        />
      </div>

      <section className="v2-section">
        <h3>Equity Curve</h3>
        <div className="v2-card">
          <EquityCurve history={history} />
        </div>
      </section>

      <section className="v2-section">
        <h3>Active Holdings ({portfolio.holdings?.length || 0})</h3>
        <div className="v2-card">
          <HoldingsTable holdings={portfolio.holdings} />
        </div>
      </section>
    </div>
  );
}
