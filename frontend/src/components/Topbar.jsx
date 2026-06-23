import { useState, useEffect } from "react";
import { BarChart3, Loader2, Play, RefreshCcw, ShieldAlert, Zap } from "lucide-react";
import { useApp } from "../context/AppContext";
import { api } from "../api";

export default function Topbar({ title, subtitle }) {
  const { status, isBusy, runAnalysis, refreshMarket, loadDashboard, selectedTicker } = useApp();
  const [tradingMode, setTradingMode] = useState(null);
  const [killActive, setKillActive] = useState(false);

  useEffect(() => {
    const fetchMode = async () => {
      try {
        const [mode, ks] = await Promise.all([
          api.getTradingMode().catch(() => null),
          api.getKillSwitch().catch(() => null),
        ]);
        setTradingMode(mode?.mode || "paper");
        setKillActive(ks?.enabled || false);
      } catch {
        // Ignore
      }
    };
    fetchMode();
    const interval = setInterval(fetchMode, 30000);
    return () => clearInterval(interval);
  }, []);

  const isLive = tradingMode === "live";

  return (
    <header className="topbar">
      <div>
        <div className="ticker-line">
          <h1>{title || "Dashboard"}</h1>
          {tradingMode && (
            <span className={`mode-indicator ${tradingMode}`}>
              {isLive ? <><Zap size={11} style={{ marginRight: 2 }} /> Live</> : "Paper"}
            </span>
          )}
          {killActive && (
            <span className="kill-switch-indicator">
              <ShieldAlert size={10} style={{ marginRight: 2 }} /> Halted
            </span>
          )}
        </div>
        {subtitle && <p>{subtitle}</p>}
      </div>

      <div className="toolbar">
        <button
          className="button secondary"
          disabled={isBusy}
          onClick={() => loadDashboard({ quiet: true, keepTicker: selectedTicker })}
        >
          <RefreshCcw size={14} className={isBusy ? "spin" : ""} />
          <span>Refresh</span>
        </button>
        <button
          className="button secondary"
          disabled={isBusy || !status.online}
          onClick={refreshMarket}
        >
          <BarChart3 size={14} />
          <span>Market</span>
        </button>
        <button
          className="button primary"
          disabled={isBusy || !status.online}
          onClick={runAnalysis}
        >
          {status.action === "Running analysis" ? (
            <Loader2 className="spin" size={14} />
          ) : (
            <Play size={14} />
          )}
          <span>Analyze</span>
        </button>
      </div>
    </header>
  );
}
