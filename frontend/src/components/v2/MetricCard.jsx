export default function MetricCard({ label, value, sub, trend }) {
  const trendClass = trend > 0 ? "v2-positive" : trend < 0 ? "v2-negative" : "";
  return (
    <div className="v2-metric-card">
      <span className="v2-metric-label">{label}</span>
      <strong className={`v2-metric-value ${trendClass}`}>{value}</strong>
      {sub && <small className="v2-metric-sub">{sub}</small>}
    </div>
  );
}
