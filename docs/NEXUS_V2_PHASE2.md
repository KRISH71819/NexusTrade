# NexusTrade v2 — PHASE 2 (Data Resilience & Storage Budget)
Branch: v2
Rule: Resilient data layer, honest health, storage budget protection.

## Objective
Eliminate 500-series errors caused by MongoDB Atlas free-tier ephemeral disconnects, enforce a strict storage budget to prevent Atlas lockouts, and provide honest, machine-readable health statuses to the frontend.

### Definition of Done (DoD)
1. **Zero 500s on DB Drops:** A global FastAPI exception handler intercepts `PyMongoError` and `AutoReconnect`, returning a clean `503 Service Unavailable` with a safe JSON payload (`{"db_online": false}`) instead of a raw stack trace.
2. **Honest Health Endpoints:** `/api/health` explicitly returns `db_online: bool` and `kill_switch_active: bool`.
3. **Qualified Universe Loader:** `history_store.py` gains a `get_qualified_universe(min_bars)` function to filter the backtest/meta-portfolio universe to only tickers with sufficient history.
4. **Storage Budget Guard:** A new `size_guard.py` module monitors the 512MB Atlas limit and automatically prunes stale `analysis_log` and `trades` documents when usage exceeds 85%.
5. **Unit Tests:** Pytest coverage for the universe filter, size guard logic, exception handler, and health endpoint degradation.
