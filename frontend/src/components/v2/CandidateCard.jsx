export default function CandidateCard({ candidate }) {
  if (!candidate) return null;
  const passed = candidate.gates?.all ?? (candidate.status === "graduated" || candidate.verdict === "PROMOTABLE");

  return (
    <div className={`v2-candidate ${passed ? "v2-pass" : "v2-fail"}`}>
      <div className="v2-candidate-header">
        <h4>{candidate.name || candidate.alpha_id || "Candidate"}</h4>
        <span className={`v2-badge ${passed ? "v2-badge-success" : "v2-badge-danger"}`}>
          {candidate.verdict || (passed ? "PASS" : "FAIL")}
        </span>
      </div>
      <p className="v2-expr" title={candidate.expression || candidate.dsl}>
        {candidate.expression || candidate.dsl || "No expression provided"}
      </p>
      <div className="v2-metrics">
        <span>Sharpe: <strong>{candidate.metrics?.sharpe != null ? Number(candidate.metrics.sharpe).toFixed(2) : (candidate.sharpe != null ? Number(candidate.sharpe).toFixed(2) : "N/A")}</strong></span>
        <span>MaxDD: <strong>{candidate.metrics?.max_dd_pct != null ? `${Number(candidate.metrics.max_dd_pct).toFixed(1)}%` : "N/A"}</strong></span>
      </div>
      {candidate.reject_reason && (
        <small className="v2-reject-reason">Reason: {candidate.reject_reason}</small>
      )}
    </div>
  );
}
