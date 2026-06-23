import { useState, useEffect } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  Activity,
  History,
  LayoutDashboard,
  Newspaper,
  Settings,
  Wallet,
  X,
  Radio,
} from "lucide-react";
import { useApp } from "../context/AppContext";
import { api } from "../api";

const navItems = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/analysis", icon: Activity, label: "Chart" },
  { to: "/portfolio", icon: Wallet, label: "Portfolio" },
  { to: "/news", icon: Newspaper, label: "News" },
  { to: "/trades", icon: History, label: "History" },
  { to: "/trading", icon: Settings, label: "Controls" },
];

export default function Sidebar({ isOpen, onClose }) {
  const { status, dashboard } = useApp();
  const location = useLocation();
  const [tradingMode, setTradingMode] = useState("paper");

  useEffect(() => {
    api.getTradingMode().then(m => setTradingMode(m?.mode || "paper")).catch(() => {});
  }, [location]);

  return (
    <>
      <div className={`sidebar-overlay ${isOpen ? "open" : ""}`} onClick={onClose} />
      <aside className={`sidebar ${isOpen ? "open" : ""}`}>
        {/* Brand */}
        <div className="brand">
          <div className="brand-mark">NT</div>
          <div>
            <strong>NexusTrade</strong>
            <span>{tradingMode === "live" ? "Live Mode" : "Paper Mode"}</span>
          </div>
          <button
            className="btn-icon"
            onClick={onClose}
            style={{ display: isOpen ? "flex" : "none", marginLeft: "auto", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", border: "none" }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Navigation */}
        <nav className="sidebar-nav">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
              onClick={onClose}
              title={label}
            >
              <Icon />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        {/* Connection Status / Market State */}
        <div className="market-state">
          <span className={`status-dot ${status.online ? "online" : ""}`} />
          <div>
            <strong>{status.online ? "Connected" : "Offline"}</strong>
            <small>
              {dashboard.watchlistInfo
                ? `${dashboard.watchlistInfo.market}`
                : "Market feed"}
            </small>
          </div>
        </div>

        {/* Footer */}
        <div className="sidebar-footer">
          v2.0
        </div>
      </aside>
    </>
  );
}
