import { Routes, Route, Navigate } from "react-router-dom";
import SystemBPage from "./pages/SystemBPage";
import ResearchPage from "./pages/ResearchPage";
import ControlsPage from "./pages/ControlsPage";
import DocsPage from "./pages/DocsPage";
import Sidebar from "./components/Sidebar";
import "./v2_styles.css";

export default function App() {
  return (
    <div className="v2-app-shell">
      <Sidebar />
      <main className="v2-main-content">
        <Routes>
          <Route path="/" element={<SystemBPage />} />
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/controls" element={<ControlsPage />} />
          <Route path="/docs" element={<DocsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
