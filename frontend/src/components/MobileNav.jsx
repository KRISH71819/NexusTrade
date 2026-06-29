import { NavLink, useLocation } from "react-router-dom";
import {
  CandlestickChart,
  LayoutDashboard,
  Briefcase,
  Newspaper,
  ClipboardList,
} from "lucide-react";

const navItems = [
  { to: "/analysis", icon: CandlestickChart, label: "Chart" },
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
  { to: "/news", icon: Newspaper, label: "News" },
  { to: "/trades", icon: ClipboardList, label: "History" },
];

export default function MobileNav() {
  const location = useLocation();

  // Also match "/" as /analysis
  const isActive = (to) => {
    if (to === "/analysis") return location.pathname === "/" || location.pathname === "/analysis";
    return location.pathname === to;
  };

  return (
    <nav className="mobile-bottom-nav">
      <div className="mobile-bottom-nav-inner">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={`mobile-nav-item ${isActive(to) ? "active" : ""}`}
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
