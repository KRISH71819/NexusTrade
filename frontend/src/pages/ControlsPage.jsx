import { useState, useEffect, useCallback } from "react";
import { Shield, ShieldAlert, Cpu, Key, RefreshCw } from "lucide-react";
import { api } from "../api";
import SpendMeter from "../components/v2/SpendMeter";

export default function ControlsPage() {
  const [tradingMode, setTradingMode] = useState("paper");
  const [killSwitch, setKillSwitch] = useState(false);
  const [dhanStatus, setDhanStatus] = useState(null);
  const [llmUsage, setLlmUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  const loadSettings = useCallback(async () => {
    try {
      const [modeRes, ksRes, dhanRes, llmRes] = await Promise.all([
        api.getTradingMode().catch(() => ({ mode: "paper" })),
        api.getKillSwitch().catch(() => ({ enabled: false })),
        api.getDhanStatus().catch(() => ({ configured: false })),
        api.getLLMUsage().catch(() => null),
      ]);
      setTradingMode(modeRes.mode || "paper");
      setKillSwitch(Boolean(ksRes.enabled));
      setDhanStatus(dhanRes);
      setLlmUsage(llmRes);
    } catch (err) {
      console.warn("Could not load controls:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  const handleToggleKillSwitch = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const nextState = !killSwitch;
      await api.toggleKillSwitch(nextState);
      setKillSwitch(nextState);
      setMessage(nextState ? "Kill Switch ENGAGED: All new buys halted." : "Kill Switch CLEARED.");
    } catch (err) {
      setMessage(`Error: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleModeChange = async (newMode) => {
    if (newMode === "live" && !dhanStatus?.configured) {
      alert("Cannot switch to Live Mode: Dhan credentials not configured.");
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      await api.setTradingMode(newMode);
      setTradingMode(newMode);
      setMessage(`Switched to ${newMode.toUpperCase()} mode.`);
    } catch (err) {
      setMessage(`Error: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="v2-page">
      <header className="v2-header">
        <div>
          <h2>System Controls & Telemetry</h2>
          <p className="v2-subtitle">Trading mode, emergency kill switch, broker credentials and budget quotas</p>
        </div>
        <button className="v2-btn v2-btn-secondary" onClick={loadSettings} title="Refresh">
          <RefreshCw size={14} /> Refresh
        </button>
      </header>

      {message && <div className="v2-banner v2-banner-success">{message}</div>}

      <div className="v2-controls-grid">
        {/* Emergency Kill Switch */}
        <div className="v2-card v2-control-card">
          <div className="v2-control-header">
            {killSwitch ? <ShieldAlert className="v2-negative" size={24} /> : <Shield className="v2-positive" size={24} />}
            <div>
              <h4>Emergency Kill Switch</h4>
              <p>Immediately blocks all live order placement and stops new paper rebalances.</p>
            </div>
          </div>
          <button
            className={`v2-btn ${killSwitch ? "v2-btn-danger" : "v2-btn-secondary"}`}
            onClick={handleToggleKillSwitch}
            disabled={saving}
          >
            {killSwitch ? "Disengage Kill Switch" : "Engage Kill Switch"}
          </button>
        </div>

        {/* Trading Mode */}
        <div className="v2-card v2-control-card">
          <div className="v2-control-header">
            <Cpu size={24} />
            <div>
              <h4>Execution Mode</h4>
              <p>Current: <strong>{tradingMode.toUpperCase()}</strong></p>
            </div>
          </div>
          <div className="v2-btn-group">
            <button
              className={`v2-btn ${tradingMode === "paper" ? "v2-btn-primary" : "v2-btn-secondary"}`}
              onClick={() => handleModeChange("paper")}
              disabled={saving}
            >
              Paper Trading
            </button>
            <button
              className={`v2-btn ${tradingMode === "live" ? "v2-btn-primary" : "v2-btn-secondary"}`}
              onClick={() => handleModeChange("live")}
              disabled={saving}
            >
              Live Trading
            </button>
          </div>
        </div>

        {/* Dhan Broker Integration */}
        <div className="v2-card v2-control-card">
          <div className="v2-control-header">
            <Key size={24} />
            <div>
              <h4>Dhan Broker Integration</h4>
              <p>Status: {dhanStatus?.configured ? "Configured" : "Unconfigured (Paper Only)"}</p>
            </div>
          </div>
          <div className="v2-status-pill">
            {dhanStatus?.configured ? "API Key Active" : "No Live Credentials Stored"}
          </div>
        </div>

        {/* AI & Storage Quota */}
        <div className="v2-card v2-control-card">
          <div className="v2-control-header">
            <Cpu size={24} />
            <div>
              <h4>Resource & Storage Quotas</h4>
              <p>MongoDB Atlas M0 Free Tier (512MB limit) & Jev AI Budget ($2/month cap)</p>
            </div>
          </div>
          <SpendMeter usage={llmUsage} />
        </div>
      </div>
    </div>
  );
}
