import { useEffect, useRef } from "react";
import { money } from "../format";
import { useApp } from "../context/AppContext";
import { useNavigate } from "react-router-dom";

/**
 * PriceTicker — Bloomberg-style horizontal scrolling price strip.
 * Shows all held stocks with live prices, change%, and flash animation.
 */
export default function PriceTicker({ prices = {}, holdings = [] }) {
  const stripRef = useRef(null);
  const prevPrices = useRef({});
  const { loadTicker } = useApp();
  const navigate = useNavigate();

  // Build display rows: held stocks with live data
  const rows = holdings
    .filter((h) => h.quantity > 0)
    .map((h) => {
      const tick = prices[h.ticker] || {};
      return {
        ticker: h.ticker.replace(".NS", ""),
        fullTicker: h.ticker,
        ltp: tick.ltp || h.current_price || h.avg_price || 0,
        change: tick.change || 0,
        changePct: tick.change_pct || 0,
        pnl: tick.ltp
          ? (tick.ltp - (h.avg_price || 0)) * (h.quantity || 0)
          : h.unrealized_pnl || 0,
      };
    });

  // Flash effect on price change
  useEffect(() => {
    if (!stripRef.current) return;
    for (const row of rows) {
      const el = stripRef.current.querySelector(`[data-ticker="${row.ticker}"]`);
      if (!el) continue;
      const prev = prevPrices.current[row.ticker];
      if (prev !== undefined && prev !== row.ltp) {
        el.classList.remove("flash-green", "flash-red");
        // Force reflow
        void el.offsetWidth;
        el.classList.add(row.ltp > prev ? "flash-green" : "flash-red");
      }
    }
    const next = {};
    for (const r of rows) next[r.ticker] = r.ltp;
    prevPrices.current = next;
  });

  if (rows.length === 0) return null;

  return (
    <div className="price-ticker-strip" ref={stripRef}>
      <div className="price-ticker-track">
        {[...rows, ...rows, ...rows].map((r, i) => (
          <div
            className="price-ticker-item"
            key={`${r.ticker}-${i}`}
            data-ticker={r.ticker}
            onClick={() => {
              loadTicker(r.fullTicker);
              navigate("/");
            }}
            style={{ cursor: "pointer" }}
          >
            <span className="ticker-symbol">{r.ticker}</span>
            <span className="ticker-price">{money(r.ltp)}</span>
            <span
              className={`ticker-change ${
                r.changePct > 0 ? "positive" : r.changePct < 0 ? "negative" : ""
              }`}
            >
              {r.changePct >= 0 ? "+" : ""}
              {r.changePct.toFixed(2)}%
            </span>
            <span
              className={`ticker-pnl ${
                r.pnl > 0 ? "positive" : r.pnl < 0 ? "negative" : ""
              }`}
            >
              {r.pnl >= 0 ? "+" : ""}
              {money(r.pnl)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
