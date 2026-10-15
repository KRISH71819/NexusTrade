# NEXUS V2 — PHASE 4 IMPLEMENTATION SPECIFICATION
**Document:** `NEXUS_V2_PHASE4.md`
**Status:** FROZEN / APPROVED FOR IMPLEMENTATION
**Scope:** EOD Paper Book, State Machine, MTM, Regime Engine, Rebalance Planner

---

## 0. Executive Summary & Anti-Scope
Phase 4 transitions NexusTrade from an intraday/LLM-driven experimental system to a **robust, strictly End-of-Day (EOD) quantitative portfolio engine**. 

**Explicit Exclusions (The "Anti-Scope"):**
*   **NO Live Dhan Order Execution:** Phase 4 is strictly paper-trading and state-machine validation. Live execution is deferred to Phase 6.
*   **NO WebSocket Tick Infrastructure:** Intraday streaming is disabled. All pricing relies on EOD yfinance/broker daily bars.
*   **NO Linear Meta-Scorer Re-fit:** Online learning and dynamic weight refitting are discarded to prevent overfitting and maintain low maintenance. Weights and rank thresholds are static and empirically gated.

---

## 1. Data Model & Persistence (MongoDB)
All V2 state is isolated from the legacy V1 collections. The `database.py` module will expose async getters for the following 7 collections:

| Collection | Purpose | Key / Index |
| :--- | :--- | :--- |
| `book` | Current portfolio state (Cash, Holdings, Avg Cost). | `_id: "main"` |
| `book_trades` | Immutable ledger of all executed paper trades. | `timestamp: -1`, `ticker: 1` |
| `book_equity` | Daily MTM snapshots for equity curve & drawdown tracking. | `date: 1` (Unique) |
| `incubator_shadow` | Shadow tracking for new alphas before full deployment. | `alpha_id: 1` |
| `research_runs` | Logs of offline alpha generation/evaluation batches. | `run_id: 1` |
| `regime_log` | Daily market regime classifications and underlying metrics. | `date: 1` (Unique) |
| `system_state_v2` | Global V2 state (Kill switch, last run timestamps). | `_id: "v2_state"` |

---

## 2. Book Engine & State Machine (`backend/core/book.py`)
The `Book` class is the single source of truth for portfolio accounting. It operates purely on EOD data.

### 2.1 Core Responsibilities
*   **Cash & Holdings Management:** Tracks available cash, locked cash (if any), and a dictionary of holdings.
*   **Average Cost Basis:** Uses standard weighted-average cost for scaling in/out.
*   **P&L Accounting:** Computes Realized P&L (on sell) and Unrealized P&L (on MTM).
*   **Trade Serialization:** Every state mutation generates a `book_trades` document with full explainability (trigger reason, regime state, rank at execution).

### 2.2 Key Methods
```python
class Book:
    async def load(): ...
    async def save(): ...
    async def apply_trade(ticker: str, side: str, qty: int, price: float, reason: str): ...
    async def mark_to_market(prices: dict[str, float]): ...
    async def get_exposure() -> float: ...
```

---

## 3. Daily MTM & Regime Engine (15:32 IST)
Executed daily at 15:32 IST, immediately after market close. 

### 3.1 Mark-to-Market & Catastrophic Stops
1.  **Fetch EOD Prices:** Pull daily OHLCV for all held tickers and the broad market index (NIFTY 500).
2.  **Catastrophic Stop Check:** 
    *   *Rule:* If `current_price <= avg_cost * 0.75` (down $\ge 25\%$), flag for immediate market-on-close (MOC) exit.
    *   *Action:* Generate `SELL` orders for catastrophic breaches *before* regime calculation.
3.  **MTM Update:** Update `book` holdings with EOD prices, calculate daily P&L, and append a snapshot to `book_equity`.

### 3.2 Regime Classification Engine
Calculates market health to dictate the target exposure for the 15:40 rebalance.

**Metrics:**
*   **Market Breadth:** $\%$ of qualified universe (e.g., NIFTY 500) trading $>$ 200-day SMA.
*   **Index Gap:** $\frac{\text{Index}_{close} - \text{SMA}_{200}}{\text{SMA}_{200}}$
*   **Vol Percentile:** Current 20-day annualized volatility vs. trailing 1-year distribution.

**Regime Matrix:**
| Regime | Condition | Target Exposure | Action |
| :--- | :--- | :--- | :--- |
| **CRISIS** | Breadth $< 20\%$ OR Index Gap $< -15\%$ | **0%** | Liquidate all to cash. |
| **RISK_OFF** | Breadth $< 40\%$ OR Index Gap $< -5\%$ | **50%** | Scale down positions. |
| **NEUTRAL** | Breadth $40\% - 70\%$ | **80%** | Maintain standard sizing. |
| **RISK_ON** | Breadth $> 70\%$ AND Index Gap $> 0\%$ | **100%** | Fully invested. |

*Outputs saved to `regime_log`.*

---

## 4. Rebalance Planner & Execution (15:40 IST)
Executed daily at 15:40 IST. Generates the target portfolio for the next trading day.

### 4.1 Cadence Gate
*   **Rule:** `should_rebalance(last_rebalance_date, today)`
*   **Logic:** Rebalance only occurs if $\ge 60$ trading days have passed since the last rebalance, OR if the regime has shifted to/from `CRISIS`.

### 4.2 Cross-Sectional Ranking & Buffers
Assuming a target portfolio size of $N=25$ stocks.
1.  **Rank Universe:** Score and rank the qualified universe based on the chosen EOD alpha (e.g., 200d trend momentum).
2.  **Entry Buffer:** Only consider stocks with **Rank $\le 25$** for new entries.
3.  **Exit Buffer:** Only force exits on current holdings if their **Rank $> 35$** (or if triggered by the 25% Catastrophic Stop). This prevents churn on the boundary.

### 4.3 Execution Constraints & Friction Models
1.  **$\pm 10\%$ No-Trade Band:** 
    *   If a current holding is in the Target Pool, only trade if the absolute difference between current weight and target weight exceeds $10\%$ of the target weight.
2.  **Cost-Aware Swap Skip:**
    *   If the planner suggests selling Stock A to buy Stock B, calculate $\Delta \text{Rank} = \text{Rank}_A - \text{Rank}_B$.
    *   If $\Delta \text{Rank} < 5$, **skip the swap**. The marginal alpha does not justify the round-trip friction (STT, slippage, spread).
3.  **30% Turnover Budget Scale-Down:**
    *   Calculate gross turnover: $T = \sum |W_{new} - W_{old}|$.
    *   If $T > \text{Turnover\_Limit}$, scale down all non-mandatory weight adjustments (drift corrections) by **30%** to preserve capital and limit market impact.

### 4.4 Order Generation
*   The planner outputs a list of `TradeIntent` objects.
*   The `Book` engine processes these intents, updates cash/holdings, and writes to `book_trades`.

---

## 5. Cadence & Scheduler Hooks (`backend/scheduler.py`)
The V2 scheduler runs strictly on EOD cron triggers, completely isolated from the legacy intraday APScheduler loops.

### 5.1 Job Definitions
```python
# 15:32 IST: MTM, Catastrophic Stops, Regime Calc
scheduler.add_job(
    func=v2_daily_mtm_and_regime,
    trigger=CronTrigger(hour=15, minute=32, day_of_week="mon-fri", timezone="Asia/Kolkata"),
    id="v2_mtm_regime",
    name="V2 EOD MTM & Regime Engine"
)

# 15:40 IST: Rebalance Planner
scheduler.add_job(
    func=v2_rebalance_planner,
    trigger=CronTrigger(hour=15, minute=40, day_of_week="mon-fri", timezone="Asia/Kolkata"),
    id="v2_rebalance",
    name="V2 EOD Rebalance Planner"
)
```

### 5.2 The Kill Switch Gate
Both jobs must be wrapped in a `@require_v2_active` decorator.
*   Reads `system_state_v2` for `kill_switch_enabled`.
*   If `True`, the MTM job still runs (to preserve accurate equity curves and catastrophic stop monitoring), but the **Rebalance Planner immediately aborts** and logs a skip event. No new trades are generated.

---

## 6. Unit Testing Strategy (`backend/tests/test_phase4_book_scheduler.py`)
All tests will use `pytest` and `pytest-asyncio`. MongoDB interactions will be mocked using `mongomock` or custom `AsyncMock` wrappers to ensure tests run in $< 2$ seconds without network I/O.

### 6.1 Test Classes & Scenarios

**`TestBookEngine`**
*   `test_avg_cost_basis_scaling`: Verify weighted average cost on partial buys/sells.
*   `test_realized_pnl_calculation`: Ensure FIFO/Avg cost P&L is accurately recorded on sells.
*   `test_cash_reserve_guard`: Verify planner cannot generate buys if cash < 0.

**`TestMTMAndCatastrophicStops`**
*   `test_catastrophic_stop_trigger`: Mock a holding dropping 26% from avg cost. Assert a `SELL` intent is generated regardless of regime or rank.
*   `test_catastrophic_stop_ignores_buffer`: Assert the 25% stop overrides the Rank $> 35$ exit buffer.
*   `test_equity_snapshot_persistence`: Verify `book_equity` receives exactly one document per MTM run.

**`TestRegimeEngine`**
*   `test_crisis_regime_liquidation`: Mock Breadth = 15%. Assert Regime = `CRISIS` and Target Exposure = 0%.
*   `test_neutral_regime_exposure`: Mock Breadth = 55%. Assert Target Exposure = 80%.

**`TestRebalancePlanner`**
*   `test_60_day_cadence_gate`: Assert planner returns `[]` intents if last rebalance was 40 days ago.
*   `test_rank_buffers_prevent_churn`: Mock a holding at Rank 28. Assert it is NOT sold (Exit buffer is $> 35$).
*   `test_no_trade_band_ignores_drift`: Mock a holding at 9.5% weight vs 10% target. Assert no trade is generated ($< 10\%$ band).
*   `test_cost_aware_swap_skip`: Mock Stock A (Rank 10) and Stock B (Rank 13). Assert swap is skipped ($\Delta \text{Rank} < 5$).
*   `test_turnover_budget_scale_down`: Mock a regime shift requiring 50% turnover. Assert the planner scales non-mandatory adjustments down by 30%.

**`TestSchedulerHooks`**
*   `test_kill_switch_blocks_rebalance`: Mock `system_state_v2` kill switch = `True`. Assert MTM runs, but Rebalance Planner aborts.

---

## 7. Implementation Sequence (Next Steps)

1.  **Scaffold Core:** Create `backend/core/` directory. Implement `book.py` and `regime.py` with pure Python logic (no DB yet).
2.  **Wire Persistence:** Update `database.py` with the 7 new V2 collection getters and index creators.
3.  **Implement Planner:** Build the cross-sectional ranking and friction-model logic in `planner.py`.
4.  **Write Tests:** Implement `test_phase4_book_scheduler.py` to lock in the math and logic boundaries.
5.  **Scheduler Integration:** Hook the jobs into `scheduler.py` with the IST timezone triggers and Kill Switch decorators.

***End of Specification. Ready for unpacking and TDD implementation.***
