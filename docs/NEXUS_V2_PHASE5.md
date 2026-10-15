# NEXUS V2 — PHASE 5 IMPLEMENTATION SPECIFICATION
**Document:** `NEXUS_V2_PHASE5.md`
**Status:** FROZEN / APPROVED FOR IMPLEMENTATION
**Scope:** V2 API Routers, 4-Page Matte-Black/Amber Web App, Legacy Clean-up & CI Verification

---

## 0. Executive Summary & Anti-Scope
Phase 5 delivers the human interface and operational control surface for NexusTrade V2. It replaces the 8 experimental legacy intraday pages with a **strictly focused, 4-page EOD quantitative cockpit**.

**Explicit Exclusions (The "Anti-Scope"):**
*   **NO WebSocket Tick Infrastructure:** Intraday live streaming and tick sockets are eliminated in favor of clean, low-overhead 60-second polling.
*   **NO Heavy Animation Libraries:** `gsap` and high-overhead canvas animations are removed to guarantee sub-second cold loads on mobile and desktop.
*   **NO Speculative Frontend State:** The frontend does not calculate P&L, exposure, or rankings client-side. The backend EOD `Book` is the single source of truth.
*   **NO Silent Zeroes:** Missing or uncalculated metrics render as `"-"`, never `0` or `0.0`.

---

## 1. Backend V2 API Layer (`backend/routers/v2.py`)
All Phase 5 endpoints are mounted under `/api/v2/` with guaranteed uptime contracts:

### 1.1 Global Contract Rules
*   Every status-like GET endpoint **must** include `{ "db_online": bool, "kill_switch_active": bool }`.
*   On database disconnect or transient failure, endpoints return HTTP 200 with `db_online: false` (or 503 from global exception handler) with safe nulls, never a 500 error or stack trace.

### 1.2 Endpoint Specifications

| Method | Endpoint | Description | Key Response Fields |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v2/overview` | Primary cockpit state for `/` | `book` (cash, equity, pnl, holdings, exposure), `regime` (state, target_exposure), `db_online`, `kill_switch_active` |
| `GET` | `/api/v2/equity` | Daily MTM series from `book_equity` | `series: [{ date, total_equity, cash, holdings_value, benchmark_return }]` |
| `GET` | `/api/v2/trades` | Paginated paper trades from `book_trades` | `trades: [{ timestamp, ticker, side, qty, price, realized_pnl, reason, rank }]` |
| `GET` | `/api/v2/research/status` | Active/latest alpha generation batch | `status`, `run_id`, `candidates: [{ expr, sharpe, max_dd, gates, reject_reason, duplicate_of }]` |
| `GET` | `/api/v2/research/hof` | Hall of Fame alphas & demotion history | `hof: [{ alpha_id, name, sleeve, sharpe, max_dd, turnover, status, demotion_reason }]` |
| `GET` | `/api/v2/research/incubator` | Active 6-week shadow alphas & correlations | `incubator: [{ alpha_id, days_in_shadow, rolling_sharpe, corr_with_sleeves }]` |
| `GET` | `/api/v2/controls/status` | System health, spend & storage telemetry | `kill_switch`, `jev_spend` (used, limit, remaining), `storage` (mb_used, limit_512mb), `scheduler_jobs` |
| `POST` | `/api/v2/controls/kill-switch` | Toggle V2 kill switch state | `{ kill_switch_enabled: bool, updated_at }` |
| `POST` | `/api/v2/research/trigger` | Manually dispatch offline alpha evolution | `{ status: "started", run_id: str }` (Clamped to max 6 candidates) |

---

## 2. Frontend 4-Page Architecture (`frontend/src/`)
The web application is condensed from 8 legacy pages into 4 clean routes:

```text
/            -> OverviewPage (Cockpit, Book State, Holdings, Equity Curve, Recent Trades)
/research    -> ResearchPage (Active Evolution Batch, Gate Checklist, Hall of Fame, Incubator)
/controls    -> ControlsPage (Emergency Kill Switch, Jev $2 Spend Meter, Mongo Storage Guard, Cron Jobs)
/docs        -> DocsPage (System Architecture, Sleeve Logic, Regime Matrix, Friction Rules)
```

### 2.1 Route Map & Structure
*   **`OverviewPage` (`/`)**:
    *   `StatusBanner`: Displays global state: `DB OFFLINE` (red), `BOOK EMPTY` (amber), `SYSTEM READY / ACTIVE` (green).
    *   `MetricCard` Grid: Total Equity (₹), Available Cash (₹), Unrealized P&L (₹ / %), Realized P&L (₹), Exposure %, Regime Badge.
    *   `EquityCurve`: Interactive chart powered by `lightweight-charts` tracking portfolio MTM vs NIFTY benchmark.
    *   `HoldingsTable`: Active holdings (Ticker, Qty, Avg Cost, EOD Price, Value ₹, P&L ₹, Allocation %).
    *   `RecentTradesTable`: Last 10 paper trades with execution rank, side badge, and explainability reason.

*   **`ResearchPage` (`/research`)**:
    *   `BatchStatusCard`: Live status and progress bar for active Jev evolution batch.
    *   `CandidateCard`: Itemized gate validation (Sharpe $\ge 1.0$, MaxDD $\le 0.75\times$, Fold Majority $> 0$, Turnover $\le 24$, Clone=False).
    *   `HofTable`: Graduated sleeves (`M`, `D`, `R`, `V`) and rolling 6-month stability scores.
    *   `IncubatorPanel`: 6-week shadow alphas and correlation heatmap with live sleeves.

*   **`ControlsPage` (`/controls`)**:
    *   `KillSwitchControl`: Prominent toggle with confirmation guard.
    *   `SpendMeter`: Jev AI token meter ($2.00 monthly budget limit, input tokens metered, cost in USD).
    *   `StorageBudgetCard`: Mongo Atlas M0 (512MB) storage monitor and prune status.
    *   `SchedulerTable`: Next execution timestamps for 15:32 MTM, 15:40 Rebalance, and Saturday Research runs.
    *   `ResearchTrigger`: One-click button to trigger scoped alpha search batch.

*   **`DocsPage` (`/docs`)**:
    *   Embedded reference manual covering the universe filters, cost models, rank buffers, and catastrophic stop mechanics.

---

## 3. Design System & Aesthetics
*   **Palette**: Matte-black (`#0A0A0C`), deep obsidian card surfaces (`#121216`), borders (`#22222A`), amber primary accent (`#F59E0B`), positive emerald (`#10B981`), negative rose (`#EF4444`).
*   **Typography**: Clean sans-serif monospace numbers (`Inter` / system-ui font stack with `tabular-nums` for financial precision).
*   **Formatting**: Strict Indian number formatting (`en-IN` format: `₹1,00,000.00`).
*   **Polling Interval**: Standard 60-second polling (`setInterval`) with manual refresh button.

---

## 4. Legacy Code Clean-up & Deletions
To eliminate bundle bloat and technical debt, the following legacy assets are deleted on the `v2` branch:
1.  **Frontend Pages**: Delete `AnalysisPage.jsx`, `DashboardPage.jsx`, `MetaPortfolioPage.jsx`, `NewsPage.jsx`, `PortfolioPage.jsx`, `TradeHistoryPage.jsx`, `TradingPage.jsx`, `TradingWorkspace.jsx`.
2.  **Dependencies**: Remove `gsap` from `package.json`.
3.  **Real-Time Churn**: Remove legacy WebSocket listener hook and replace with REST polling.

---

## 5. Automated Verification & Testing Strategy
1.  **Backend Router Tests (`backend/tests/test_phase5_api_routers.py`)**:
    *   `test_overview_endpoint`: Validates portfolio math, holdings serialization, and honest `db_online` payload.
    *   `test_overview_db_offline_fallback`: Verifies 200 response with `db_online: false` when DB is unreachable.
    *   `test_equity_endpoint_ordering`: Verifies chronological MTM snapshots.
    *   `test_trades_pagination`: Validates recent paper trades filtering.
    *   `test_kill_switch_toggle`: Verifies `POST /api/v2/controls/kill-switch` updates `system_state_v2`.
    *   `test_research_trigger_bounds`: Verifies `POST /api/v2/research/trigger` clamps `count` to max 6 candidates.
2.  **Frontend Compilation**:
    *   `npm run build` succeeds in `< 10` seconds with zero syntax or bundling errors.

---

## 6. Implementation Sequence

1.  **Scaffold Backend V2 Router**: Implement `backend/routers/v2.py` with the 9 core endpoints and mount in `backend/main.py`.
2.  **Write API Tests**: Create `backend/tests/test_phase5_api_routers.py` and ensure 100% green.
3.  **Clean Legacy Frontend**: Remove `gsap` from `package.json` and delete the 8 legacy page files.
4.  **Build V2 Components**: Build `StatusBanner`, `MetricCard`, `HoldingsTable`, `EquityCurve`, `CandidateCard`, `SpendMeter`, `HofTable`.
5.  **Build 4 Core Pages**: Implement `OverviewPage`, `ResearchPage`, `ControlsPage`, and `DocsPage`.
6.  **Verify Frontend Bundle**: Run `npm run build` to confirm production bundle compiles cleanly.
7.  **Full Suite Verification**: Run `python -m pytest tests -q` to guarantee zero regressions.

***End of Specification. Ready for approval and execution.***
