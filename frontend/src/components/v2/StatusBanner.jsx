import { AlertTriangle, Database, ShieldAlert, CheckCircle } from "lucide-react";

export default function StatusBanner({ telemetry }) {
  if (!telemetry) return null;
  const { db_online, kill_switch_active } = telemetry;

  if (!db_online) {
    return (
      <div className="v2-banner v2-banner-danger">
        <Database size={16} /> <strong>DB OFFLINE:</strong> Running in degraded mode. Data may be stale.
      </div>
    );
  }
  if (kill_switch_active) {
    return (
      <div className="v2-banner v2-banner-warning">
        <ShieldAlert size={16} /> <strong>KILL SWITCH ACTIVE:</strong> New live buys are blocked.
      </div>
    );
  }
  return (
    <div className="v2-banner v2-banner-success">
      <CheckCircle size={16} /> System Nominal
    </div>
  );
}
