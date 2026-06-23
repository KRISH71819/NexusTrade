import Topbar from "../components/Topbar";
import TradingModePanel from "../components/TradingModePanel";

export default function TradingPage() {
  return (
    <>
      <Topbar title="Trading Controls" subtitle="Manage live/paper mode, kill switch, and broker connection" />
      <TradingModePanel />
    </>
  );
}
