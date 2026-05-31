import { BarChart3, Loader2, Play, RefreshCcw } from "lucide-react";
import { useApp } from "../context/AppContext";

export default function Topbar({ title, subtitle }) {
  const { status, isBusy, runAnalysis, refreshMarket, loadDashboard, selectedTicker } = useApp();

  return (
    <header className="topbar">
      <div>
        <div className="ticker-line">
          <h1>{title || "Dashboard"}</h1>
        </div>
        {subtitle && <p>{subtitle}</p>}
      </div>

      <div className="toolbar">
        <button
          className="button secondary"
          disabled={isBusy}
          onClick={() => loadDashboard({ quiet: true, keepTicker: selectedTicker })}
        >
          <RefreshCcw size={15} />
          <span>Refresh</span>
        </button>
        <button
          className="button secondary"
          disabled={isBusy || !status.online}
          onClick={refreshMarket}
        >
          <BarChart3 size={15} />
          <span>Market</span>
        </button>
        <button
          className="button primary"
          disabled={isBusy || !status.online}
          onClick={runAnalysis}
        >
          {status.action === "Running analysis" ? <Loader2 className="spin" size={15} /> : <Play size={15} />}
          <span>Analyze</span>
        </button>
      </div>
    </header>
  );
}
