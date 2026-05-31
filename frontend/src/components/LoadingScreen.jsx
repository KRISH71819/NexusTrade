import { Loader2 } from "lucide-react";

export default function LoadingScreen() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        gap: "16px",
        background: "var(--bg)",
      }}
    >
      <div style={{ position: "relative" }}>
        <div
          style={{
            width: 64,
            height: 64,
            borderRadius: "var(--radius-lg)",
            background: "linear-gradient(135deg, var(--blue) 0%, var(--purple) 100%)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "1.5rem",
            fontWeight: 800,
            color: "white",
            boxShadow: "0 4px 16px rgba(41, 121, 255, 0.3)",
          }}
        >
          NT
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <Loader2 className="spin" size={18} style={{ color: "var(--blue)" }} />
        <span style={{ color: "var(--text-secondary)", fontSize: "14px" }}>
          Connecting to NexusTrade AI...
        </span>
      </div>
    </div>
  );
}
