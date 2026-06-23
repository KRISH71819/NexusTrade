import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  History,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { gsap } from "gsap";
import { useApp } from "../context/AppContext";
import Topbar from "../components/Topbar";
import ActionPill from "../components/ActionPill";
import EmptyState from "../components/EmptyState";
import {
  dateTime,
  money,
  percent,
  probability,
  signedClass,
} from "../format";

export default function TradeHistoryPage() {
  const { tradeHistory, tradeHistoryPage, loadTradeHistory, dispatch } = useApp();
  const [expandedRow, setExpandedRow] = useState(null);
  const trades = tradeHistory?.trades || [];
  const totalPages = tradeHistory?.total_pages || 1;
  const contentRef = useRef(null);

  useEffect(() => {
    loadTradeHistory(tradeHistoryPage, 30);
  }, [tradeHistoryPage, loadTradeHistory]);

  useEffect(() => {
    if (contentRef.current) {
      gsap.fromTo(
        contentRef.current,
        { opacity: 0, y: 15 },
        {
          opacity: 1,
          y: 0,
          duration: 0.4,
          ease: "power3.out",
        }
      );
    }
  }, [tradeHistoryPage]);

  const setPage = (p) => dispatch({ type: "SET_TRADE_PAGE", payload: p });

  return (
    <>
      <Topbar title="Trade History" subtitle={`${tradeHistory.total_count || 0} total trades`} />
      <div ref={contentRef}>
        <div className="detail-panel">
          <div className="panel-title-row compact">
            <div>
              <h2>
                <History size={16} />
                Trade Log
              </h2>
              <span>Paginated trade history with realized P&L and strategy signals</span>
            </div>
            <div style={{ fontSize: "11px", color: "var(--muted)" }}>
              Page {tradeHistoryPage} of {totalPages}
            </div>
          </div>

          <div className="table-scroll" style={{ marginTop: "12px" }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 28 }}></th>
                  <th>Date</th>
                  <th>Ticker</th>
                  <th>Action</th>
                  <th>Qty</th>
                  <th>Price</th>
                  <th>Total</th>
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
                      <TradeRow
                        key={`${trade.timestamp}-${trade.ticker}-${idx}`}
                        trade={trade}
                        hasPnl={hasPnl}
                        isExpanded={isExpanded}
                        onToggle={() => setExpandedRow(isExpanded ? null : idx)}
                      />
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan="9">
                      <EmptyState
                        title="No trade history"
                        text="Run analysis cycles to generate trades."
                      />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="pagination" style={{ marginTop: "12px" }}>
              <button disabled={tradeHistoryPage <= 1} onClick={() => setPage(tradeHistoryPage - 1)}>
                <ChevronLeft size={14} />
              </button>
              {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                const num = i + 1;
                return (
                  <button
                    key={num}
                    className={tradeHistoryPage === num ? "active" : ""}
                    onClick={() => setPage(num)}
                  >
                    {num}
                  </button>
                );
              })}
              {totalPages > 7 && <span style={{ color: "var(--muted)", padding: "0 4px" }}>…</span>}
              <button disabled={tradeHistoryPage >= totalPages} onClick={() => setPage(tradeHistoryPage + 1)}>
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function TradeRow({ trade, hasPnl, isExpanded, onToggle }) {
  const { loadTicker } = useApp();
  const navigate = useNavigate();
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }}>
        <td style={{ padding: "10px 6px" }}>
          <ChevronDown
            size={14}
            style={{
              transition: "transform 200ms ease",
              transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
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
              navigate("/");
            }}
            title={`Inspect ${trade.ticker}`}
          >
            {trade.ticker}
          </strong>
        </td>
        <td><ActionPill action={trade.action} /></td>
        <td>{Number(trade.quantity || 0).toLocaleString("en-IN")}</td>
        <td>{money(trade.price)}</td>
        <td>{money(trade.total_value)}</td>
        <td className={hasPnl ? signedClass(trade.realized_pnl) : ""}>
          {hasPnl ? (
            <span>
              {money(trade.realized_pnl)} ({percent(trade.realized_pnl_pct)})
            </span>
          ) : (
            <span style={{ color: "var(--muted)" }}>—</span>
          )}
        </td>
        <td>{trade.final_score ? trade.final_score.toFixed(2) : "—"}</td>
      </tr>
      {isExpanded && (
        <tr className="trade-row-expanded">
          <td colSpan="9">
            <div className="trade-reasoning-label">Strategy Reasoning</div>
            <p style={{ marginBottom: "8px", fontSize: "12px", lineHeight: "1.5" }}>
              {trade.ai_reasoning || "No reasoning recorded for this trade."}
            </p>
            <div className="trade-reasoning-scores">
              <span>ML Conf: <strong>{probability(trade.ml_confidence)}</strong></span>
              <span>LLM Conf: <strong>{probability(trade.gemini_confidence)}</strong></span>
              <span>Score: <strong>{trade.final_score?.toFixed(3) || "—"}</strong></span>
              {trade.crisis_detected && <span style={{ color: "var(--red)" }}>⚠ CRISIS DETECTED</span>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
