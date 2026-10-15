# NEXUS V2 — PHASE 6 IMPLEMENTATION SPECIFICATION
**Document:** `NEXUS_V2_PHASE6.md`
**Status:** COMPLETE / FROZEN
**Scope:** Cutover & Migration, MongoDB Collection Archival, Dhan Live Safety Gates, Production Runbook

---

## 0. Executive Summary & Anti-Scope
Phase 6 represents the final transition from NexusTrade V1 (intraday LLM heuristic trader) to NexusTrade V2 (strictly End-of-Day quantitative portfolio engine).

**Explicit Invariants & Anti-Scope:**
*   **STANDING RULE #8 IS SACROSANCT:** The collection `ohlcv_history` is NEVER dropped, renamed, or altered. It serves as the immutable daily price store for all backtests and live MTM evaluations.
*   **NO Live Dhan Execution Without 6-Gate Clearance:** Live orders can never reach Dhan APIs unless all 6 gates pass (Mode=live, KillSwitch=off, Book=OPEN, MarketHours=open, Valid Credentials, and Valid Order Sanity).
*   **Idempotent Migration:** Re-running the migration script is completely safe and will never corrupt or overwrite archived collections.

---

## 1. Migration & Collection Archival (`tools/migrate_v1_to_v2.py`)

### 1.1 Collection Mapping

| V1 Collection | Action | V2 Destination / Name |
| :--- | :--- | :--- |
| `ohlcv_history` | **PRESERVE UNTOUCHED** | `ohlcv_history` (Standing Rule #8) |
| `trades` | Rename / Archive | `legacy_v1_trades` |
| `portfolio` | Rename / Archive | `legacy_v1_portfolio` |
| `analyses` / `analysis` | Rename / Archive | `legacy_v1_analyses` |
| `market_data` | Rename / Archive | `legacy_v1_market_data` |
| `watchlist` | Rename / Archive | `legacy_v1_watchlist` |
| `snapshots` | Rename / Archive | `legacy_v1_snapshots` |
| `gemini_cache` | Rename / Archive | `legacy_v1_gemini_cache` |
| `paper_orders` | Rename / Archive | `legacy_v1_paper_orders` |

### 1.2 Target V2 Collections & Indexes

*   **`eod_book`**: Holds current EOD portfolio state (cash, initial capital, watermark, holdings dict, status).
    *   Index: `{ "book_id": 1 }` (Unique)
*   **`eod_snapshots`**: Daily MTM equity snapshots for performance and drawdown tracking.
    *   Index: `{ "date": -1 }`
*   **`alpha_hof`**: Hall of Fame promoted alphas with historical Sharpe, turnover, and validation metrics.
    *   Index: `{ "alpha_id": 1 }` (Unique), `{ "status": 1 }`
*   **`research_runs`**: Historical record of Jev evolution batches and candidate backtests.
    *   Index: `{ "run_id": 1 }` (Unique), `{ "created_at": -1 }`
*   **`audit_log`**: Structured log of rebalance decisions, catastrophic stop events, and regime changes.
    *   Index: `{ "timestamp": -1 }`, `{ "event_type": 1 }`

---

## 2. Dhan Live Execution Safety Gate (`backend/core/dhan_gate.py`)
All order execution routes must pass through `can_execute_live_order(...)`:

1.  **Gate 1 (`MODE_GATE`)**: `trading_mode == "live"`. Any other mode ("paper", "backtest") hard-stops live routing.
2.  **Gate 2 (`KILL_SWITCH_GATE`)**: `kill_switch_on == False`. Emergency stop blocks all buys immediately.
3.  **Gate 3 (`BOOK_STATUS_GATE`)**: `book_status == "OPEN"`. If book is "HALTED" (due to catastrophic stop -4% daily or -15% watermark), live execution is rejected.
4.  **Gate 4 (`MARKET_HOURS_GATE`)**: Current IST time must fall on a weekday (Monday–Friday) between 09:15:00 and 15:30:00 IST. Weekends and post-market hours are rejected.
5.  **Gate 5 (`CREDENTIAL_GATE`)**: Client ID, PIN, TOTP Secret, and Access Token must be non-empty and well-formed.
6.  **Gate 6 (`ORDER_SANITY_GATE`)**: Ticker must be non-empty, Quantity must be positive integer, Price must be positive float, Side must be "BUY" or "SELL".

---

## 3. Production Cutover Runbook

```bash
# Step 1: Run pre-flight check across all 209 unit tests
cd backend
python -m pytest tests -q

# Step 2: Dry-run migration to inspect planned actions
cd ..
python tools/migrate_v1_to_v2.py --dry-run

# Step 3: Execute live MongoDB collection cutover
python tools/migrate_v1_to_v2.py --live

# Step 4: Verify frontend production build
cd frontend
npm run build

# Step 5: Start backend and verify V2 overview endpoint
cd ../backend
uvicorn main:app --port 8000
curl http://127.0.0.1:8000/api/v2/health
curl http://127.0.0.1:8000/api/v2/portfolio
```

---

## 4. Phase Verification Matrix
*   **Migration Unit Tests:** 4/4 passing (`test_ohlcv_history_never_dropped_or_renamed`, `test_migration_dry_run_does_not_mutate`, `test_migration_idempotent_when_target_already_exists`, `test_migration_initializes_v2_indexes`).
*   **Dhan Gate Unit Tests:** 8/8 passing (mode, kill switch, book status, weekend, market hours, credentials, order sanity, full passage).
*   **Frontend Production Build:** Vite v6.4.2 clean compile (`dist/index.html` 1.67 kB, `dist/assets/index.js` 372 kB) in under 10s.
*   **Total Suite:** 209 tests passing, 0 failures.
