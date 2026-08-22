import { NavLink } from "react-router-dom";
import {
  CandlestickChart,
  TrendingUp,
  Briefcase,
  Newspaper,
  ClipboardList,
} from "lucide-react";

const navItems = [
  { to: "/", icon: TrendingUp, label: "System B", end: true },
  { to: "/analysis", icon: CandlestickChart, label: "Chart" },
  { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
  { to: "/news", icon: Newspaper, label: "News" },
  { to: "/trades", icon: ClipboardList, label: "History" },
];

export default function MobileNav() {
  return (
    <nav className="mobile-bottom-nav">
      <div className="mobile-bottom-nav-inner">
        {navItems.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => `mobile-nav-item ${isActive ? "active" : ""}`}
            style={{ textDecoration: "none" }}
          >
            <Icon size={20} />
            <span>{label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
