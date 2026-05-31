import { ShieldCheck } from "lucide-react";

export default function EmptyState({ icon, title, text, action }) {
  return (
    <div className="empty-block" style={{ textAlign: "center", justifyItems: "center" }}>
      {icon || <ShieldCheck size={24} />}
      <strong>{title}</strong>
      <span>{text}</span>
      {action}
    </div>
  );
}
