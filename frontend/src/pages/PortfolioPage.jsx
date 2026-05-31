import { useEffect, useRef } from "react";
import {
  RefreshCcw,
  Shield,
  ShieldAlert,
  Target,
  Wallet,
} from "lucide-react";
import { gsap } from "gsap";
import { useApp } from "../context/AppContext";
import Topbar from "../components/Topbar";
import EmptyState from "../components/EmptyState";
import { money, percent, signedClass } from "../format";

export default function PortfolioPage() {
  const { dashboard, pnlData, capitalAmount, dispatch, resetPaperAccount, isBusy, status } = useApp();
  const portfolio = dashboard.portfolio || {};
  const holdings = portfolio.holdings || [];
  const riskStatus = portfolio.risk_status || {};
  const sectorAllocation = portfolio.sector_allocation || {};
  const contentRef = useRef(null);

  useEffect(() => {
    if (contentRef.current) {
      gsap.fromTo(
        contentRef.current.children,
        { opacity: 0, y: 20 },
        {
          opacity: 1,
          y: 0,
          stagger: 0.08,
          duration: 0.5,
          ease: "power3.out",
        }
      );
    }
  }, [status.loading]);

  return (
    <>
      <Topbar title="Portfolio" subtitle="Holdings, risk management & capital controls" />
      <div ref={contentRef}>
        {/* Risk Status Banner */}
        <div className="detail-panel" style={{ marginBottom: "16px", padding: "14px" }}>
          <div style={{
            display: "flex",
            alignItems: "center",
            gap: "24px",
            flexWrap: "wrap",
            marginBottom: "12px",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              {riskStatus.buying_halted ? <ShieldAlert size={20} className="negative" /> : <Shield size={20} className="positive" />}
              <div>
                <strong style={{ fontSize: "14px" }} className={riskStatus.buying_halted ? "negative" : "positive"}>
                  {riskStatus.buying_halted ? "BUYING HALTED" : "Risk Active"}
                </strong>
                <div style={{ fontSize: "11px", color: "var(--muted)", marginTop: "2px" }}>
                  Drawdown {portfolio.drawdown_pct?.toFixed(1) || "0.0"}% / {(riskStatus.drawdown_limit || 15).toFixed(0)}%
                </div>
              </div>
            </div>

            <div style={{ display: "flex", gap: "16px", fontSize: "13px", color: "var(--text-secondary)", flexWrap: "wrap" }}>
              <span>Stop-Loss: <strong style={{ color: "var(--text)" }}>{riskStatus.stop_loss_pct?.toFixed(0) || 7}%</strong></span>
              <span>Trailing: <strong style={{ color: "var(--text)" }}>8%</strong></span>
              <span>Max/Sector: <strong style={{ color: "var(--text)" }}>{riskStatus.max_sector_stocks || 3}</strong></span>
            </div>

            {riskStatus.buying_halted && (
              <span className="action-pill sell" style={{ fontSize: "10px" }}>HALTED</span>
            )}
          </div>

          {/* Drawdown Progress */}
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div style={{ flex: 1, height: 6, background: "var(--panel-3)", borderRadius: "999px", overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  width: `${Math.min(100, ((portfolio.drawdown_pct || 0) / (riskStatus.drawdown_limit || 15)) * 100)}%`,
                  background: (portfolio.drawdown_pct || 0) > (riskStatus.drawdown_limit || 15) * 0.7 ? "var(--red)" : "var(--green)",
                  borderRadius: "999px",
                  transition: "width 0.6s ease",
                }}
              />
            </div>
            <span style={{ fontSize: "11px", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
              {portfolio.drawdown_pct?.toFixed(1) || "0.0"}%
            </span>
          </div>
        </div>

        <div className="detail-grid">
          {/* Holdings */}
          <div className="detail-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <Wallet size={16} />
                  Holdings ({holdings.length})
                </h2>
                <span>Live-priced positions with trailing stop protection</span>
              </div>
            </div>

            {/* Sector Allocation */}
            {sectorAllocation && Object.keys(sectorAllocation).length > 0 && (
              <div style={{ padding: "8px 0", display: "flex", gap: 6, flexWrap: "wrap", borderBottom: "1px solid var(--border-subtle)" }}>
                {Object.entries(sectorAllocation).map(([sector, value]) => (
                  <span key={sector} style={{ background: "var(--panel-2)", padding: "2px 8px", borderRadius: 4, fontSize: "11px", border: "1px solid var(--border)" }}>
                    {sector}: <strong>{money(value)}</strong>
                  </span>
                ))}
              </div>
            )}

            <div className="holdings-list" style={{ marginTop: "12px" }}>
              {holdings.length > 0 ? (
                holdings.map((h) => {
                  const peak = h.peak_price || h.avg_price || 0;
                  const current = h.current_price || h.avg_price || 0;
                  const stopPrice = peak * 0.92;
                  const distPct = current > 0 ? ((current - stopPrice) / current) * 100 : 0;
                  const clamped = Math.max(0, Math.min(100, distPct));
                  const zone = clamped > 5 ? "safe" : clamped > 2 ? "warning" : "danger";

                  return (
                    <div className="holding-row" key={h.ticker} style={{ display: "grid" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                        <div>
                          <strong>{h.ticker}</strong>
                          <small style={{ display: "block" }}>
                            {h.quantity} shares @ avg {money(h.avg_price)}
                            {h.sector && <span style={{ marginLeft: 6, opacity: 0.6 }}>• {h.sector}</span>}
                          </small>
                        </div>
                        <div style={{ textAlign: "right" }}>
                          <strong className={signedClass(h.unrealized_pnl)}>{money(h.market_value)}</strong>
                          <small className={signedClass(h.unrealized_pnl)} style={{ display: "block" }}>
                            {money(h.unrealized_pnl)} ({percent(h.unrealized_pnl_pct)})
                          </small>
                        </div>
                      </div>
                      {peak > 0 && (
                        <div className="stop-distance">
                          <Target size={12} style={{ color: "var(--muted)", flexShrink: 0 }} />
                          <div className="stop-bar-track">
                            <div className={`stop-bar-fill ${zone}`} style={{ width: `${Math.min(100, clamped * 10)}%` }} />
                          </div>
                          <span className={`stop-label ${zone}`}>{distPct.toFixed(1)}% to stop</span>
                        </div>
                      )}
                    </div>
                  );
                })
              ) : (
                <div className="holding-row">
                  <div>
                    <strong>No open holdings</strong>
                    <small>Portfolio is fully liquid</small>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <strong>{money(portfolio.cash)}</strong>
                    <small>Cash</small>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Capital Controls */}
          <div className="detail-panel">
            <div className="panel-title-row compact">
              <div>
                <h2>
                  <RefreshCcw size={16} />
                  Capital Controls
                </h2>
                <span>Reset paper portfolio and starting capital</span>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "16px", marginTop: "16px" }}>
              {/* P&L Summary */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <div style={{ fontSize: "11px", color: "var(--muted)", marginBottom: 4 }}>Total P&L</div>
                  <div className={`mono ${signedClass(pnlData.total_pnl)}`} style={{ fontSize: "20px", fontWeight: 700, fontFamily: "JetBrains Mono, monospace" }}>
                    {money(pnlData.total_pnl)}
                  </div>
                  <div className={`mono ${signedClass(pnlData.total_pnl_pct)}`} style={{ fontSize: "11px", fontFamily: "JetBrains Mono, monospace" }}>
                    {percent(pnlData.total_pnl_pct)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "11px", color: "var(--muted)", marginBottom: 4 }}>Portfolio Value</div>
                  <div className="mono" style={{ fontSize: "20px", fontWeight: 700, fontFamily: "JetBrains Mono, monospace" }}>
                    {money(portfolio.total_value)}
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--muted)" }}>
                    Cash: {money(portfolio.cash)}
                  </div>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <div style={{ fontSize: "11px", color: "var(--muted)", marginBottom: 4 }}>Realized</div>
                  <div className={`mono ${signedClass(pnlData.total_realized_pnl)}`} style={{ fontWeight: 600, fontFamily: "JetBrains Mono, monospace" }}>
                    {money(pnlData.total_realized_pnl)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "11px", color: "var(--muted)", marginBottom: 4 }}>Unrealized</div>
                  <div className={`mono ${signedClass(pnlData.total_unrealized_pnl)}`} style={{ fontWeight: 600, fontFamily: "JetBrains Mono, monospace" }}>
                    {money(pnlData.total_unrealized_pnl)}
                  </div>
                </div>
              </div>

              <div style={{ borderTop: "1px solid var(--border)", paddingTop: "16px" }}>
                <label style={{ fontSize: "11px", color: "var(--muted)", display: "block", marginBottom: 8, fontWeight: 700, textTransform: "uppercase" }}>
                  Starting Capital (₹)
                </label>
                <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                  <div className="capital-control" style={{ flex: 1 }}>
                    <span>₹</span>
                    <input
                      type="number"
                      min="1"
                      step="10000"
                      value={capitalAmount}
                      onChange={(e) => dispatch({ type: "SET_CAPITAL", payload: e.target.value })}
                    />
                  </div>
                  <button
                    className="button"
                    style={{ background: "var(--red)", color: "white", padding: "0 16px", height: "38px" }}
                    disabled={isBusy || !status.online}
                    onClick={resetPaperAccount}
                  >
                    <RefreshCcw size={14} style={{ marginRight: 6 }} className={isBusy ? "spin" : ""} />
                    Reset
                  </button>
                </div>
                <p style={{ fontSize: "11px", color: "var(--muted)", marginTop: 8 }}>
                  This clears all trades, analysis logs, and P&L history.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
