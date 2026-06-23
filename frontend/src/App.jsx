import { useState } from "react";
import { Routes, Route } from "react-router-dom";
import TradingWorkspace from "./pages/TradingWorkspace";
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
        <Route index element={null} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="analysis" element={null} />
        <Route path="portfolio" element={<PortfolioPage />} />
        <Route path="news" element={<NewsPage />} />
        <Route path="trades" element={<TradeHistoryPage />} />
        <Route path="trading" element={<TradingPage />} />
      </Route>
      <Route path="*" element={<TradingWorkspace />} />
    </Routes>
  );
}
