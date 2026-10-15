import { useState, useEffect, useCallback } from "react";
import { Play, RefreshCw, Cpu, Award } from "lucide-react";
import { api } from "../api";
import CandidateCard from "../components/v2/CandidateCard";

export default function ResearchPage() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [message, setMessage] = useState(null);

  const loadStatus = useCallback(async () => {
    try {
      const data = await api.researchStatus();
      setStatus(data);
    } catch (err) {
      console.warn("Could not load research status:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 30000);
    return () => clearInterval(interval);
  }, [loadStatus]);

  const handleTrigger = async () => {
    setTriggering(true);
    setMessage(null);
    try {
      const res = await api.triggerResearch(6);
      setMessage(`Batch started! Run ID: ${res.run_id || "active"}`);
      await loadStatus();
    } catch (err) {
      setMessage(`Error triggering batch: ${err.message}`);
    } finally {
      setTriggering(false);
    }
  };

  const candidates = status?.candidates || [];

  return (
    <div className="v2-page">
      <header className="v2-header">
        <div>
          <h2>Alpha Research & Evolution</h2>
          <p className="v2-subtitle">Jev SystemOne offline alpha generation, gate critique & incubation</p>
        </div>
        <div className="v2-header-actions">
          <button className="v2-btn v2-btn-secondary" onClick={loadStatus} title="Refresh">
            <RefreshCw size={14} /> Refresh
          </button>
          <button
            className="v2-btn v2-btn-primary"
            onClick={handleTrigger}
            disabled={triggering || status?.status === "running"}
          >
            <Play size={14} /> {triggering ? "Starting..." : "Trigger Batch (Cap 6)"}
          </button>
        </div>
      </header>

      {message && <div className="v2-banner v2-banner-success">{message}</div>}

      <div className="v2-metric-grid">
        <div className="v2-metric-card">
          <span className="v2-metric-label">Batch State</span>
          <strong className="v2-metric-value">{status?.status || "Idle"}</strong>
          {status?.run_id && <small className="v2-metric-sub">Run: {status.run_id}</small>}
        </div>
        <div className="v2-metric-card">
          <span className="v2-metric-label">Model Engine</span>
          <strong className="v2-metric-value">Jev SystemOne</strong>
          <small className="v2-metric-sub">Bounded queries ($2/mo hard cap)</small>
        </div>
        <div className="v2-metric-card">
          <span className="v2-metric-label">Candidates Evaluated</span>
          <strong className="v2-metric-value">{candidates.length}</strong>
          <small className="v2-metric-sub">Latest batch</small>
        </div>
      </div>

      <section className="v2-section">
        <h3>Evaluated Candidates</h3>
        {candidates.length === 0 ? (
          <div className="v2-card v2-empty">
            <Cpu size={32} />
            <p>No research candidates found yet. Click "Trigger Batch" to generate alphas.</p>
          </div>
        ) : (
          <div className="v2-candidate-grid">
            {candidates.map((c, idx) => (
              <CandidateCard key={c.alpha_id || idx} candidate={c} />
            ))}
          </div>
        )}
      </section>

      {status?.log_tail?.length > 0 && (
        <section className="v2-section">
          <h3>Execution Trace</h3>
          <div className="v2-card v2-log-box">
            {status.log_tail.map((line, i) => (
              <div key={i} className="v2-log-line">{line}</div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
