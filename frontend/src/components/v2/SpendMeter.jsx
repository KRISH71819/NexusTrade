export default function SpendMeter({ usage }) {
  if (!usage) return null;
  const callsToday = usage.calls_today ?? 0;
  const dailyLimit = usage.daily_limit ?? 1500;
  const pct = Math.min(100, Math.max(0, (callsToday / dailyLimit) * 100));

  return (
    <div className="v2-spend-meter">
      <div className="v2-spend-label">
        <span>LLM Usage: <strong>{callsToday} / {dailyLimit}</strong> calls</span>
        <span>{pct.toFixed(0)}%</span>
      </div>
      <div className="v2-progress-track">
        <div
          className="v2-progress-fill"
          style={{
            width: `${pct}%`,
            background: pct > 80 ? "#ef4444" : "#d4a853",
          }}
        />
      </div>
    </div>
  );
}
