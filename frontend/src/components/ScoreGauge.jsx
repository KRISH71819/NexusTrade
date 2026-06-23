import { useEffect, useState } from "react";
import { probability, sentiment, signedClass } from "../format";

export default function ScoreGauge({
  label,
  max,
  min,
  sentimentMode = false,
  threshold,
  value,
}) {
  const [animatedValue, setAnimatedValue] = useState(min);

  useEffect(() => {
    const timer = setTimeout(() => {
      setAnimatedValue(value);
    }, 50);
    return () => clearTimeout(timer);
  }, [value, min]);

  const range = max - min;
  const percent = clamp((animatedValue - min) / range, 0, 1);
  const angle = -120 + percent * 240;
  const display = sentimentMode ? sentiment(value) : probability(value);
  const tone = sentimentMode ? signedClass(value) : value >= threshold ? "positive" : "";

  return (
    <div className="gauge-card">
      <svg viewBox="0 0 180 112" role="img" aria-label={`${label}: ${display}`}>
        <path className="gauge-track" d="M 24 92 A 66 66 0 0 1 156 92" />
        <path
          className={`gauge-value ${tone}`}
          d="M 24 92 A 66 66 0 0 1 156 92"
          pathLength="100"
          style={{ strokeDasharray: `${percent * 100} 100` }}
        />
        <line
          className="gauge-needle"
          x1="90"
          y1="92"
          x2="90"
          y2="40"
          style={{ transform: `rotate(${angle}deg)`, transformOrigin: "90px 92px" }}
        />
        <circle cx="90" cy="92" r="4.5" fill="var(--text)" />
      </svg>
      <span>{label}</span>
      <strong className={tone}>{display}</strong>
      <small>Gate {sentimentMode ? sentiment(threshold) : probability(threshold)}</small>
    </div>
  );
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
