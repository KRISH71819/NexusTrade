import { NavLink } from "react-router-dom";
import { TrendingUp, Cpu, SlidersHorizontal, BookOpen } from "lucide-react";

const navItems = [
  { to: "/", icon: TrendingUp, label: "System B", end: true },
  { to: "/research", icon: Cpu, label: "Research" },
  { to: "/controls", icon: SlidersHorizontal, label: "Controls" },
  { to: "/docs", icon: BookOpen, label: "Documentation" },
];

export default function Sidebar() {
  return (
    <aside className="v2-sidebar">
      <div className="v2-sidebar-brand">
        <span className="v2-brand-badge">NT</span>
        <div>
          <h1>NexusTrade</h1>
        </div>
      </div>

      <nav className="v2-sidebar-nav">
        {navItems.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => `v2-nav-item ${isActive ? "active" : ""}`}
            title={label}
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="v2-sidebar-footer">
        NexusTrade v2.0 • EOD Portfolio Engine
      </div>
    </aside>
  );
}
