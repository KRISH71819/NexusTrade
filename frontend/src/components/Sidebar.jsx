import { useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  History,
  LayoutDashboard,
  Newspaper,
  Search,
  Wallet,
  X,
} from "lucide-react";
import { useApp } from "../context/AppContext";
import { money } from "../format";
import ActionPill from "./ActionPill";

const navItems = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/analysis", icon: Activity, label: "Quant Analysis" },
  { to: "/portfolio", icon: Wallet, label: "Portfolio" },
  { to: "/news", icon: Newspaper, label: "Market News" },
  { to: "/trades", icon: History, label: "Trade History" },
];

export default function Sidebar({ isOpen, onClose }) {
  const { status, dashboard, watchlistRows, selectedTicker, loadTicker } = useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");

  const filtered = search
    ? watchlistRows.filter((r) =>
        r.ticker.toLowerCase().includes(search.toLowerCase())
      )
    : watchlistRows;

  const handleTickerSelect = (ticker) => {
    loadTicker(ticker);
    if (location.pathname !== "/analysis") {
      navigate("/analysis");
    }
    onClose();
  };

  return (
    <>
      <div className={`sidebar-overlay ${isOpen ? "open" : ""}`} onClick={onClose} />
      <aside className={`sidebar ${isOpen ? "open" : ""}`}>
        {/* Brand */}
        <div className="brand">
          <div className="brand-mark">NT</div>
          <div>
            <strong>NexusTrade</strong>
            <span>Paper Trading Workspace</span>
          </div>
          <button
            className="btn-icon"
            onClick={onClose}
            style={{ display: isOpen ? "flex" : "none", marginLeft: "auto", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", border: "none" }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Connection Status / Market State */}
        <div className="market-state">
          <span className={`status-dot ${status.online ? "online" : ""}`} />
          <div>
            <strong>{status.online ? "Live — Connected" : "Backend Offline"}</strong>
            <small>
              {dashboard.watchlistInfo
                ? `${dashboard.watchlistInfo.market} • ${dashboard.watchlistInfo.market_hours}`
                : "Checking connection..."}
            </small>
          </div>
        </div>

        {/* Navigation */}
        <nav className="sidebar-nav">
          <div className="nav-section-label">Workspace</div>
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
              onClick={onClose}
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Watchlist */}
        <div className="watchlist-search" style={{ marginTop: "24px" }}>
          <Search size={14} style={{ marginRight: "6px" }} />
          <span>Watchlist ({filtered.length})</span>
        </div>

        {/* Watchlist Search Input */}
        <div style={{ padding: "0 4px 10px" }}>
          <input
            type="text"
            placeholder="Search tickers..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: "100%",
              padding: "8px 12px",
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              color: "var(--text)",
              fontSize: "12px",
              outline: "none",
            }}
          />
        </div>

        <div className="watchlist">
          {filtered.length > 0 ? (
            filtered.map((row) => {
              const isActive = selectedTicker && row.ticker.toUpperCase() === selectedTicker.toUpperCase();
              return (
                <button
                  key={row.ticker}
                  className={`watchlist-item ${isActive ? "active" : ""}`}
                  onClick={() => handleTickerSelect(row.ticker)}
                  type="button"
                >
                  <div style={{ minWidth: 0, flex: 1, textOverflow: "ellipsis", overflow: "hidden" }}>
                    <strong style={{ textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }}>{row.ticker}</strong>
                    <small style={{ textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap", display: "block" }}>
                      {row.company || "Stock Ticker"}
                    </small>
                  </div>
                  <div className="watchlist-right">
                    <span>{row.price ? money(row.price) : "--"}</span>
                    <ActionPill action={row.action} />
                  </div>
                </button>
              );
            })
          ) : (
            <div style={{ color: "var(--muted)", fontSize: "11px", textAlign: "center", padding: "12px" }}>
              No tickers found
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="sidebar-footer">
          NexusTrade v2.0 • Paper Trading
        </div>
      </aside>
    </>
  );
}
