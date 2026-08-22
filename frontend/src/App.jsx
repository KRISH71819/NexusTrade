import { Routes, Route, Navigate } from "react-router-dom";
import TradingWorkspace from "./pages/TradingWorkspace";
import MetaPortfolioPage from "./pages/MetaPortfolioPage";
import DashboardPage from "./pages/DashboardPage";
import PortfolioPage from "./pages/PortfolioPage";
import NewsPage from "./pages/NewsPage";
import TradeHistoryPage from "./pages/TradeHistoryPage";
import TradingPage from "./pages/TradingPage";
import "./old_components.css";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<TradingWorkspace />}>
        {/* System B — default landing */}
        <Route index element={<MetaPortfolioPage />} />
        <Route path="chart" element={null} />
        <Route path="analysis" element={null} />
        {/* System A — legacy (frozen) */}
        <Route path="legacy" element={<DashboardPage />} />
        <Route path="dashboard" element={<Navigate to="/legacy" replace />} />
        <Route path="portfolio" element={<PortfolioPage />} />
        <Route path="news" element={<NewsPage />} />
        <Route path="trades" element={<TradeHistoryPage />} />
        <Route path="trading" element={<TradingPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
