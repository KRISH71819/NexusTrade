import { signedClass } from "../format";

export default function MetricCard({ icon, label, value, sub, trend, glow }) {
  const glowClass =
    glow && trend !== undefined
      ? Number(trend) >= 0
        ? "pnl-positive"
        : "pnl-negative"
      : "";

  return (
    <article className={`metric ${glowClass}`}>
      <div className="metric-label">
        <div style={{ display: "flex", alignItems: "center" }}>{icon}</div>
        <span>{label}</span>
      </div>
      <strong className={trend !== undefined ? signedClass(trend) : ""}>
        {value}
      </strong>
      <small className={trend === undefined ? "" : signedClass(trend)}>
        {sub}
      </small>
    </article>
  );
}
