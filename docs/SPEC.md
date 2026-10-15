# NexusTrade v2 — Frozen SPEC (condensed, Phase 1 sign-off)
## 1. Constraints
NSE equities only. HF Spaces free CPU backend. Mongo Atlas M0 512MB. Vercel frontend.
Spend: Jev API only, hard cap $2/month, metered at GET /controls/status via X-Tokens-Remaining.
Forbidden: Opus-class models, paid data/vendors/hosts, RL, crypto stacks, intraday entries,
per-minute loops, hourly LLM calls.
## 2. Universe
qualified = seeded AND bars >= 1500 AND 20d median turnover >= Rs 5Cr AND price >= Rs 50.
Target 100-250 names. Research panel ALWAYS >= 4x top_n (top_n=25 => panel >= 100).
## 3. Book
Top-25 equal-weight from sleeve composite. 60 trading-day rebalance.
Rank buffer: enter rank <= 25, exit rank > 35. No-trade band +/-10%.
Turnover budget <= 30% of book per rebalance (proportional scale-down).
Cost-aware skip: rank delta < 5 => no swap.
Name-level catastrophic stop: -25% from avg cost, checked at daily MTM, code-only.
## 4. Overlays & regime (code-only)
Composite 200d trend: off => exposure 0. Vol target 15%, scale clipped 0.25-1.0.
Regime ladder from breadth (% qualified > 200d SMA) + index gap vs 200d + realized vol pctile:
RISK_ON cap 1.0 | NEUTRAL 0.8 | RISK_OFF 0.4 | CRISIS 0.0.
CRISIS if breadth < 35 OR index > 6% below 200d OR vol pctile > 90.
Jev may propose bounded override (+/- one step), logged to regime_log, ratified Sunday gate.
## 5. Sleeves
M = trend_200 rank (validated, capital day-1).
D = low-vol defensive (negated 120d vol among trend-positive). R = rev_21. V = volmom_60.
D/R/V start in incubator. Blend = equal-weight among graduated, monthly reweight by trailing
inverse-vol. Max 4 sleeves.
## 6. Research loop
Code template bank -> Jev choice/score selector (bounded) -> structural critic (code) ->
Jev adversarial critic (choice) -> sandbox (walk-forward + costs + stress 2008/2020/vol-spike +
duplicate fingerprint) -> gates (sharpe>=1.0, maxDD<=0.75x bench, fold majority>0,
turnover<=24 gate/12 target, clone=false, universe rule) -> incubator shadow 6 weeks
(graduate iff incubation Sharpe>=0.8 AND corr with existing sleeves<0.8) -> Sunday human gate.
Demotion: rolling 6m Sharpe<0.5 halves capital; <0 demotes with reason. HoF = graduated only.
Jev wire contract: POST https://jevmodel.org/v1/systemone, Bearer $JEVMODEL_API_KEY,
body {model, state, questions{name:{type,instructions,criteria}}}, <=8 questions/request,
state <=8000 chars. Batch all questions for one state in ONE request.
Retry 429/502 with exponential backoff + same Idempotency-Key. 402 => Jev down path.
Log model + usage.input_tokens + X-Tokens-Remaining into research_runs / regime_log.
## 7. Cadence (IST)
15:32 daily MTM + regime pass + nightly Jev note (report only). 15:40 cadence-gated rebalance
check. Sat 10:00 scoped batch cap 6 (evolution_auto OFF default, manual trigger always).
Sun 18:00 weekly pack + human gate.
## 8. Collections
New: book, book_trades, book_equity, incubator_shadow, regime_log, review_notes.
Dev-suffix: system_state_v2, alpha_registry_v2, hall_of_fame_v2 (consolidated at cutover).
Reused read-only: ohlcv_history. Cutover (P6): rename v1 cols -> archive_v1_*, drop
market_data/analysis_log/score_history/cycle_stats/news_intelligence, migrate holdings.
## 9. API contract rule
EVERY status-like GET returns db_online + kill_switch_active. DB error => HTTP 200 with
db_online:false (never 500, never silent empty). Missing field renders "-" in UI, never 0.
## 10. Frontend (P5)
4 pages: / Overview | /research | /controls | /docs. One StatusBanner (DB OFFLINE red /
BOOK EMPTY amber / OK green) from db_online + book state only. Components: MetricCard,
HoldingsTable, EquityCurve, RunCard, CandidateCard (gates ITEMIZED + reject_reason +
duplicate_of), HofTable (demotion history), IncubatorPanel, RegimeBadge, SpendMeter,
ChecklistCard. Keep lightweight-charts, react-router, INR en-IN formatters, matte-black+amber.
Drop gsap, WebSocket tick infra, all 8 legacy pages. Poll 60s.
## 11. Storage budget
Atlas M0 512MB (~203MB used). ohlcv_history protected. research_runs capped (log_tail 40),
review_notes capped, incubator snapshots pruned after decision. size_guard script in P2.
## 12. Phase DoD
P1: core pure + pytest green (this file). P2: mongo wrapper heal + history store + health
endpoints honest. P3: registry/incubator/HoF + jev adapter mocked-then-live. P4: paper book +
MTM + planner + kill switch + scheduler cadence. P5: api routers + 4-page web + CI + legacy
deletions on v2 branch. P6: cutover migration script + provenance doc.
