import { useState, useEffect, useCallback } from "react";
import { api } from "../api";
import {
  Shield,
  ShieldAlert,
  Radio,
  Wallet,
  ToggleLeft,
  ToggleRight,
  RefreshCcw,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Loader2,
  Zap,
  TrendingUp,
  Key,
  LogOut,
  X,
  Lock,
} from "lucide-react";

export default function TradingModePanel() {
  const [tradingMode, setTradingMode] = useState(null);
  const [killSwitch, setKillSwitch] = useState(null);
  const [dhanStatus, setDhanStatus] = useState(null);
  const [dhanFunds, setDhanFunds] = useState(null);
  const [dhanCreds, setDhanCreds] = useState(null);
  const [loading, setLoading] = useState(true);
  const [switching, setSwitching] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [capitalCap, setCapitalCap] = useState(null);
  const [savingCap, setSavingCap] = useState(false);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // Credential modal state
  const [showCredModal, setShowCredModal] = useState(false);
  const [credForm, setCredForm] = useState({
    client_id: "",
    pin: "",
    totp_secret: "",
    access_token: "",
  });
  const [savingCreds, setSavingCreds] = useState(false);
  const [credError, setCredError] = useState("");

  const loadAll = useCallback(async () => {
    try {
      const [mode, ks, capRes, creds] = await Promise.all([
        api.getTradingMode().catch(() => null),
        api.getKillSwitch().catch(() => null),
        api.getCapitalCap().catch(() => null),
        api.getDhanCredentials().catch(() => null),
      ]);
      setTradingMode(mode);
      setKillSwitch(ks);
      if (capRes) setCapitalCap(capRes.cap);
      setDhanCreds(creds);

      if (mode?.dhan_configured || creds?.configured) {
        const [status, funds] = await Promise.all([
          api.getDhanStatus().catch(() => null),
          api.getDhanFunds().catch(() => null),
        ]);
        setDhanStatus(status);
        setDhanFunds(funds);
      }
    } catch (e) {
      setError("Could not load trading mode data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Auto-clear messages
  useEffect(() => {
    if (successMsg) {
      const timer = setTimeout(() => setSuccessMsg(""), 4000);
      return () => clearTimeout(timer);
    }
  }, [successMsg]);

  // ── Credential Save ──
  const handleSaveCredentials = async () => {
    if (!credForm.client_id.trim()) {
      setCredError("Client ID is required");
      return;
    }
    if (!credForm.totp_secret.trim() && !credForm.access_token.trim()) {
      setCredError("Provide either TOTP Secret (for auto-login) or Access Token");
      return;
    }

    setSavingCreds(true);
    setCredError("");
    try {
      await api.saveDhanCredentials(
        credForm.client_id.trim(),
        credForm.pin.trim(),
        credForm.totp_secret.trim(),
        credForm.access_token.trim()
      );
      setShowCredModal(false);
      setSuccessMsg("Dhan credentials saved! Connecting...");
      setCredForm({ client_id: "", pin: "", totp_secret: "", access_token: "" });

      // Now try switching to live
      await handleModeSwitch("live", true);
      await loadAll();
    } catch (e) {
      setCredError(e.message || "Failed to save credentials");
    } finally {
      setSavingCreds(false);
    }
  };

  // ── Credential Delete ──
  const handleDisconnectDhan = async () => {
    const confirmed = window.confirm(
      "⚠️ DISCONNECT DHAN ACCOUNT\n\n" +
        "This will delete your saved credentials and switch to paper trading.\n\n" +
        "Are you sure?"
    );
    if (!confirmed) return;

    try {
      await api.deleteDhanCredentials();
      setSuccessMsg("Dhan account disconnected. Switched to paper mode.");
      await loadAll();
    } catch (e) {
      setError(e.message || "Failed to disconnect");
    }
  };

  // ── Mode Switch ──
  const handleModeSwitch = async (newMode, skipConfirm = false) => {
    if (switching) return;

    if (newMode === "live" && !skipConfirm) {
      // Check if credentials exist
      const creds = await api.getDhanCredentials().catch(() => null);
      if (!creds?.configured) {
        // Show credential modal instead of blocking
        setShowCredModal(true);
        return;
      }

      const confirmed = window.confirm(
        "⚠️ SWITCHING TO LIVE TRADING\n\n" +
          "This will use REAL MONEY from your Dhan account.\n" +
          "The bot will place actual buy/sell orders.\n\n" +
          "Are you absolutely sure?"
      );
      if (!confirmed) return;
    }

    setSwitching(true);
    setError("");
    try {
      const result = await api.setTradingMode(newMode);
      setSuccessMsg(result.message || `Switched to ${newMode.toUpperCase()} mode`);
      await loadAll();
    } catch (e) {
      // If live switch failed because no creds, show modal
      if (newMode === "live" && e.message?.includes("credentials")) {
        setShowCredModal(true);
      } else {
        setError(e.message || "Failed to switch mode");
      }
    } finally {
      setSwitching(false);
    }
  };

  const handleKillSwitch = async () => {
    const newState = !killSwitch?.enabled;
    const action = newState ? "ACTIVATE" : "DEACTIVATE";

    if (newState) {
      const confirmed = window.confirm(
        `🚨 ${action} KILL SWITCH?\n\n` +
          "This will BLOCK all new buy orders in live mode.\n" +
          "Sell orders will continue to execute normally.\n\n" +
          "Use this for emergencies only."
      );
      if (!confirmed) return;
    }

    try {
      const result = await api.toggleKillSwitch(newState);
      setKillSwitch((prev) => ({ ...prev, enabled: newState }));
      setSuccessMsg(result.message || `Kill switch ${action}D`);
    } catch (e) {
      setError(e.message || "Failed to toggle kill switch");
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      await api.syncDhanPortfolio();
      setSuccessMsg("Live portfolio synced with Dhan account");
      const funds = await api.getDhanFunds().catch(() => null);
      setDhanFunds(funds);
    } catch (e) {
      setError(e.message || "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const handleSetCapitalCap = async (isFullInvestment, newCapVal = 0) => {
    setSavingCap(true);
    try {
      const val = isFullInvestment ? 0 : Number(newCapVal);
      const res = await api.setCapitalCap(val);
      setCapitalCap(res.cap);
      setSuccessMsg(res.message || "Investment cap updated");
    } catch (e) {
      setError(e.message || "Failed to update investment cap");
    } finally {
      setSavingCap(false);
    }
  };

  if (loading) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
        <Loader2 className="spin" size={24} />
        <p style={{ marginTop: "0.5rem", opacity: 0.7 }}>Loading trading controls...</p>
      </div>
    );
  }

  const currentMode = tradingMode?.mode || "paper";
  const isLive = currentMode === "live";
  const killActive = killSwitch?.enabled ?? false;
  const dhanConfigured = dhanCreds?.configured || tradingMode?.dhan_configured || false;

  // Parse Dhan funds
  const fundsData = dhanFunds?.data || dhanFunds || {};
  const availableBalance = Number(
    fundsData.availabelBalance || fundsData.availableBalance || fundsData.sodLimit || 0
  );

  return (
    <div className="trading-mode-panel">
      {/* ── Status Messages ── */}
      {error && (
        <div className="alert alert-danger" style={{ marginBottom: "1rem" }}>
          <XCircle size={16} /> {error}
          <button onClick={() => setError("")} style={{ marginLeft: "auto", background: "none", border: "none", color: "inherit", cursor: "pointer" }}>✕</button>
        </div>
      )}
      {successMsg && (
        <div className="alert alert-success" style={{ marginBottom: "1rem" }}>
          <CheckCircle size={16} /> {successMsg}
        </div>
      )}

      {/* ── Mode Switcher ── */}
      <div className="card" style={{ borderLeft: isLive ? "3px solid var(--color-danger)" : "3px solid var(--color-up)" }}>
        <div className="card-header" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <Radio size={18} />
          <h3 style={{ margin: 0 }}>Trading Mode</h3>
          <span
            className={`badge ${isLive ? "badge-danger" : "badge-success"}`}
            style={{ marginLeft: "auto" }}
          >
            {isLive ? "🔴 LIVE" : "📝 PAPER"}
          </span>
        </div>

        <div className="mode-switch-container" style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
          <button
            className={`button ${!isLive ? "primary" : "secondary"}`}
            onClick={() => handleModeSwitch("paper")}
            disabled={switching || !isLive}
            style={{ flex: 1 }}
          >
            <TrendingUp size={16} />
            Paper Trading
          </button>
          <button
            className={`button ${isLive ? "danger" : "secondary"}`}
            onClick={() => handleModeSwitch("live")}
            disabled={switching || isLive}
            style={{ flex: 1 }}
          >
            {switching ? <Loader2 className="spin" size={16} /> : <Zap size={16} />}
            Live Trading
          </button>
        </div>

        {isLive && (
          <div className="live-warning" style={{
            marginTop: "0.75rem",
            padding: "0.5rem 0.75rem",
            background: "rgba(239, 68, 68, 0.1)",
            borderRadius: "6px",
            fontSize: "0.8rem",
            color: "var(--color-danger)",
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
          }}>
            <AlertTriangle size={14} />
            <span>LIVE MODE — Real money orders are being placed on your Dhan account</span>
          </div>
        )}

        {!dhanConfigured && !isLive && (
          <p style={{
            marginTop: "0.75rem",
            fontSize: "0.8rem",
            opacity: 0.6,
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
          }}>
            <Key size={14} />
            Click &quot;Live Trading&quot; to enter your Dhan credentials and enable real trading.
          </p>
        )}

        {dhanConfigured && !isLive && (
          <p style={{
            marginTop: "0.75rem",
            fontSize: "0.8rem",
            color: "var(--color-up)",
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
          }}>
            <CheckCircle size={14} />
            Dhan account connected ({dhanCreds?.client_id || "configured"}). Click &quot;Live Trading&quot; to switch.
          </p>
        )}
      </div>

      {/* ── Kill Switch ── */}
      <div className="card" style={{
        marginTop: "1rem",
        borderLeft: killActive ? "3px solid var(--color-danger)" : "3px solid var(--color-muted)",
      }}>
        <div className="card-header" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          {killActive ? <ShieldAlert size={18} color="var(--color-danger)" /> : <Shield size={18} />}
          <h3 style={{ margin: 0 }}>Kill Switch</h3>
          <span
            className={`badge ${killActive ? "badge-danger" : "badge-muted"}`}
            style={{ marginLeft: "auto" }}
          >
            {killActive ? "🚨 ACTIVE" : "✅ OFF"}
          </span>
        </div>

        <p style={{ fontSize: "0.8rem", opacity: 0.7, margin: "0.5rem 0" }}>
          {killActive
            ? "All new live BUY orders are blocked. Paper trading continues normally."
            : "Normal trading. The kill switch halts all live buys instantly when activated."}
        </p>

        <button
          className={`button ${killActive ? "primary" : "danger"}`}
          onClick={handleKillSwitch}
          style={{ width: "100%" }}
        >
          {killActive ? (
            <><ToggleRight size={16} /> Resume Trading</>
          ) : (
            <><ToggleLeft size={16} /> Emergency Stop</>
          )}
        </button>
      </div>

      {/* ── Dhan Account ── */}
      {dhanConfigured && (
        <div className="card" style={{ marginTop: "1rem" }}>
          <div className="card-header" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <Wallet size={18} />
            <h3 style={{ margin: 0 }}>Dhan Account</h3>
            <div style={{ marginLeft: "auto", display: "flex", gap: "0.5rem" }}>
              <button
                className="button secondary"
                onClick={handleSync}
                disabled={syncing}
                style={{ padding: "0.25rem 0.5rem", fontSize: "0.75rem" }}
              >
                {syncing ? <Loader2 className="spin" size={14} /> : <RefreshCcw size={14} />}
                Sync
              </button>
              <button
                className="button secondary"
                onClick={handleDisconnectDhan}
                style={{ padding: "0.25rem 0.5rem", fontSize: "0.75rem", color: "var(--color-danger)" }}
                title="Disconnect Dhan account and clear credentials"
              >
                <LogOut size={14} />
                Disconnect
              </button>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem", marginTop: "0.75rem" }}>
            <div className="stat-mini">
              <span className="stat-label">Available Balance</span>
              <span className="stat-value" style={{ color: "var(--color-up)" }}>
                ₹{availableBalance.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </span>
            </div>
            <div className="stat-mini">
              <span className="stat-label">Connection</span>
              <span className="stat-value" style={{
                color: dhanStatus?.connected ? "var(--color-up)" : "var(--color-danger)"
              }}>
                {dhanStatus?.connected ? "✅ Connected" : "⚠️ Offline"}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ── Investment Cap ── */}
      {isLive && dhanConfigured && (
        <div className="card" style={{ marginTop: "1rem" }}>
          <div className="card-header" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <Wallet size={18} />
            <h3 style={{ margin: 0 }}>Investment Cap</h3>
            <span
              className={`badge ${capitalCap <= 0 ? "badge-danger" : "badge-success"}`}
              style={{ marginLeft: "auto" }}
            >
              {capitalCap <= 0 ? "FULL ACCOUNT" : "CAPPED"}
            </span>
          </div>

          <p style={{ fontSize: "0.8rem", opacity: 0.7, margin: "0.5rem 0" }}>
            Control how much of your Dhan balance the bot is allowed to use.
          </p>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginTop: "1rem" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
              <input 
                type="radio" 
                name="capType" 
                checked={capitalCap <= 0} 
                onChange={() => handleSetCapitalCap(true)} 
                disabled={savingCap}
              />
              <strong>Full Investment</strong> (Use all available balance)
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
              <input 
                type="radio" 
                name="capType" 
                checked={capitalCap > 0} 
                onChange={() => {
                  const amt = window.prompt("Enter maximum amount to invest (e.g., 100000):", capitalCap > 0 ? capitalCap : 100000);
                  if (amt && !isNaN(amt)) handleSetCapitalCap(false, amt);
                }} 
                disabled={savingCap}
              />
              <strong>Fixed Cap</strong> {capitalCap > 0 ? `(₹${capitalCap.toLocaleString("en-IN")})` : ""}
            </label>
          </div>
        </div>
      )}

      {/* ── Credential Modal ── */}
      {showCredModal && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0, 0, 0, 0.7)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 9999,
            backdropFilter: "blur(4px)",
          }}
          onClick={(e) => { if (e.target === e.currentTarget) setShowCredModal(false); }}
        >
          <div
            style={{
              background: "var(--panel)",
              borderRadius: "var(--radius-lg)",
              padding: "2rem",
              width: "min(480px, 90vw)",
              border: "1px solid var(--border)",
              boxShadow: "var(--shadow-lg)",
            }}
          >
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.5rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                <div style={{
                  width: "40px",
                  height: "40px",
                  borderRadius: "var(--radius)",
                  background: "linear-gradient(135deg, var(--accent) 0%, var(--green) 100%)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}>
                  <Key size={20} color="#0c0d12" />
                </div>
                <div>
                  <h3 style={{ margin: 0, fontSize: "1.1rem", fontFamily: "Geist, sans-serif" }}>Link Dhan Account</h3>
                  <p style={{ margin: 0, fontSize: "0.75rem", color: "var(--text-secondary)" }}>Secure API credentials connection via Dhan Broker API</p>
                </div>
              </div>
              <button
                onClick={() => setShowCredModal(false)}
                style={{ background: "none", border: "none", color: "var(--text-secondary)", cursor: "pointer", padding: "0.25rem" }}
              >
                <X size={20} />
              </button>
            </div>

            {/* Error */}
            {credError && (
              <div className="alert alert-danger" style={{ marginBottom: "1rem", fontSize: "0.85rem" }}>
                <XCircle size={14} /> {credError}
              </div>
            )}

            {/* Form */}
            <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
              <div className="form-input-group">
                <label>Client ID *</label>
                <input
                  type="text"
                  placeholder="e.g. 1111930165"
                  value={credForm.client_id}
                  onChange={(e) => setCredForm({ ...credForm, client_id: e.target.value })}
                />
              </div>

              <div className="form-input-group">
                <label>PIN</label>
                <input
                  type="password"
                  placeholder="Your Dhan login PIN"
                  value={credForm.pin}
                  onChange={(e) => setCredForm({ ...credForm, pin: e.target.value })}
                />
              </div>

              {/* TOTP Section */}
              <div style={{
                padding: "1rem",
                borderRadius: "var(--radius)",
                background: "var(--accent-soft)",
                border: "1px solid rgba(16, 185, 129, 0.2)",
              }}>
                <div className="form-input-group">
                  <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--green)" }}>
                    <Lock size={14} />
                    TOTP Secret (recommended)
                  </label>
                  <input
                    type="password"
                    placeholder="Base32 TOTP secret from Dhan"
                    value={credForm.totp_secret}
                    onChange={(e) => setCredForm({ ...credForm, totp_secret: e.target.value })}
                  />
                </div>
                <p style={{ margin: "0.5rem 0 0", fontSize: "0.72rem", color: "var(--text-secondary)", lineHeight: "1.3" }}>
                  Used to securely automate your daily login token generation.
                </p>
              </div>

              {/* OR divider */}
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", margin: "0.25rem 0" }}>
                <div style={{ flex: 1, height: "1px", background: "var(--border)" }} />
                <span style={{ fontSize: "0.75rem", color: "var(--muted)" }}>OR</span>
                <div style={{ flex: 1, height: "1px", background: "var(--border)" }} />
              </div>

              {/* Access Token Section */}
              <div className="form-input-group">
                <label>Access Token (fallback)</label>
                <input
                  type="password"
                  placeholder="Paste token from api.dhan.co"
                  value={credForm.access_token}
                  onChange={(e) => setCredForm({ ...credForm, access_token: e.target.value })}
                />
                <p style={{ margin: "0.4rem 0 0", fontSize: "0.72rem", color: "var(--text-secondary)" }}>
                  Paste access token from{" "}
                  <a href="https://api.dhan.co" target="_blank" rel="noreferrer" style={{ color: "var(--accent)", fontWeight: 700 }}>
                    api.dhan.co
                  </a>
                  . Valid for 24 hours.
                </p>
              </div>
            </div>

            {/* Actions */}
            <div style={{ display: "flex", gap: "0.75rem", marginTop: "1.75rem" }}>
              <button
                className="button secondary"
                onClick={() => setShowCredModal(false)}
                style={{ flex: 1 }}
              >
                Cancel
              </button>
              <button
                className="button primary"
                onClick={handleSaveCredentials}
                disabled={savingCreds}
                style={{ flex: 2 }}
              >
                {savingCreds ? (
                  <><Loader2 className="spin" size={16} /> Connecting...</>
                ) : (
                  <><Zap size={16} /> Link dhan account</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
