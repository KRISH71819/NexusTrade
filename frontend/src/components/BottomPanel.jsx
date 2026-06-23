import React, { useState } from 'react';
import { History, ChevronDown } from 'lucide-react';
import { useApp } from '../context/AppContext';
import { dateTime, money, percent, probability, signedClass } from '../format';

export default function BottomPanel({ currentSymbol }) {
  const { tradeHistory, loadTicker } = useApp();
  const [expandedRow, setExpandedRow] = useState(null);
  
  const trades = tradeHistory?.trades || [];

  return (
    <div className="bottom-panel">
      <div className="bottom-panel-header">
        <History size={14} color="var(--text-secondary)" />
        <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Trade History & Reasoning</span>
      </div>
      <div className="bottom-panel-content">
        <table className="bottom-trade-table">
          <thead>
            <tr>
              <th style={{ width: 28 }}></th>
              <th>Date</th>
              <th>Ticker</th>
              <th>Action</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Realized P&L</th>
              <th>Score</th>
            </tr>
          </thead>
          <tbody>
            {trades.length > 0 ? (
              trades.map((trade, idx) => {
                const isExpanded = expandedRow === idx;
                const hasPnl = trade.realized_pnl !== null && trade.realized_pnl !== undefined;
                return (
                  <React.Fragment key={`${trade.timestamp}-${trade.ticker}-${idx}`}>
                    <tr onClick={() => setExpandedRow(isExpanded ? null : idx)} style={{ cursor: "pointer" }}>
                      <td>
                        <ChevronDown
                          size={14}
                          style={{
                            transition: "transform 200ms ease",
                            transform: isExpanded ? "rotate(180deg)" : "rotate(-90deg)",
                            color: "var(--muted)",
                          }}
                        />
                      </td>
                      <td>{dateTime(trade.timestamp)}</td>
                      <td>
                        <strong
                          style={{ cursor: "pointer", color: "var(--accent)" }}
                          onClick={(e) => {
                            e.stopPropagation();
                            loadTicker(trade.ticker);
                          }}
                        >
                          {trade.ticker}
                        </strong>
                      </td>
                      <td>
                        <span className={`trade-action-badge ${trade.action.toLowerCase()}`}>
                          {trade.action}
                        </span>
                      </td>
                      <td>{Number(trade.quantity || 0).toLocaleString()}</td>
                      <td>{money(trade.price)}</td>
                      <td className={hasPnl ? signedClass(trade.realized_pnl) : ""}>
                        {hasPnl ? (
                          <span>{money(trade.realized_pnl)} ({percent(trade.realized_pnl_pct)})</span>
                        ) : (
                          <span style={{ color: "var(--muted)" }}>—</span>
                        )}
                      </td>
                      <td>{trade.final_score ? trade.final_score.toFixed(2) : "—"}</td>
                    </tr>
                    {isExpanded && (
                      <tr className="trade-row-expanded">
                        <td colSpan="8">
                          <div className="trade-reasoning-container">
                            <div className="trade-reasoning-label">AI Strategy Reasoning</div>
                            <p>{trade.ai_reasoning || trade.gemini_explanation || "No reasoning recorded for this trade."}</p>
                            <div className="trade-reasoning-scores">
                              <span>ML Conf: <strong>{probability(trade.ml_confidence)}</strong></span>
                              <span>LLM Conf: <strong>{probability(trade.gemini_confidence)}</strong></span>
                              <span>Score: <strong>{trade.final_score?.toFixed(3) || "—"}</strong></span>
                              {trade.crisis_detected && <span style={{ color: "var(--red)" }}>⚠ CRISIS DETECTED</span>}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            ) : (
              <tr>
                <td colSpan="8" style={{ textAlign: 'center', padding: '20px', color: 'var(--muted)', fontSize: '12px' }}>
                  No trade history available. Run analysis cycles to generate trades.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
