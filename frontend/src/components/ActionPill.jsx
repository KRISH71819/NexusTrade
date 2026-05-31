import { actionClass } from "../format";

export default function ActionPill({ action }) {
  const cls = actionClass(action);
  return <span className={`action-pill ${cls}`}>{action || "HOLD"}</span>;
}
