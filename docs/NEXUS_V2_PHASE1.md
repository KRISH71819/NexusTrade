# NexusTrade v2 — PHASE 1 (core math layer)
Branch: v2 (create with `git checkout -b v2` from main BEFORE applying).
Rule: ADDITIVE ONLY. Do not edit or delete any existing file in this phase.
Verify after apply: `cd backend && python -m pytest tests -q` must be fully green.
Roadmap: P1 core (this file) → P2 data+health → P3 research+incubator+jev → P4 book+scheduler+risk → P5 api+web+CI+legacy deletions → P6 cutover migration.

== DECISIONS FROZEN IN THIS PHASE ==
- One AI only: Jev (typed questions: choice / score / noul). No Groq, no Gemma, no XGBoost, no news sentiment.
- Generator = code template bank + Jev bounded selector (Jev cannot write free-form DSL).
- Code gates are supreme. Jev down => research skips, book runs code-only.
- Dev collections use _v2 suffix for the 3 shared names (system_state_v2, alpha_registry_v2, hall_of_fame_v2). ohlcv_history shared read-only, never dropped.

== FILE LIST (12 files) ==
docs/SPEC.md, backend/core/__init__.py, backend/core/ist.py, backend/core/costs.py,
backend/core/dsl.py, backend/core/gates.py, backend/core/universe.py, backend/core/regime.py,
backend/core/portfolio.py, backend/core/sleeves.py, backend/tests/conftest.py,
backend/tests/test_core_costs_dsl.py, backend/tests/test_core_gates_universe.py,
backend/tests/test_core_portfolio_sleeves.py, backend/tests/test_core_regime_ist.py

===FILE: docs/SPEC.md===
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
===END===

===FILE: backend/core/__init__.py===
# NexusTrade v2 core layer — pure, zero-I/O math. No DB, no network, no AI imports here.
===END===

===FILE: backend/core/ist.py===
"""IST time helpers (ported verbatim from legacy market_time.py).
HF containers run UTC; every trading-day rollover must use IST."""
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

def ist_now() -> datetime:
    return datetime.now(IST)

def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)

def ist_today_str() -> str:
    return ist_now().strftime("%Y-%m-%d")

def ist_day_start_utc(now: datetime | None = None) -> datetime:
    now_ist = to_ist(now) if now is not None else ist_now()
    midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_ist.astimezone(timezone.utc)

def is_stale_day_open(stored_date_str: str | None, current_date_str: str | None = None) -> bool:
    if not stored_date_str:
        return True
    today = current_date_str or ist_today_str()
    return stored_date_str != today
===END===

===FILE: backend/core/costs.py===
"""Indian delivery-equity cost model (ported verbatim math from legacy ledger.py).
Pure: rates are arguments with frozen defaults, never imported from config."""
RATES = {
    "stt_buy_pct": 0.001,            # STT 0.1% buy
    "stt_sell_pct": 0.001,           # STT 0.1% sell
    "exchange_txn_charge_pct": 0.0000345,
    "sebi_turnover_fee_pct": 0.000001,
    "stamp_duty_buy_pct": 0.00015,   # stamp 0.015% buy only
    "brokerage_per_order": 20.0,     # Rs 20 per order
    "dp_charge_per_sell": 15.0,      # Rs 15 per scrip, sell only
    "gst_pct": 0.18,                 # GST on brokerage + txn + DP
}
DEFAULT_SLIPPAGE_BPS = 40.0

def calculate_trade_charges(turnover: float, side: str, rates: dict | None = None) -> dict:
    r = rates or RATES
    stt = turnover * (r["stt_buy_pct"] if side == "BUY" else r["stt_sell_pct"])
    exchange_txn = turnover * r["exchange_txn_charge_pct"]
    sebi_fee = turnover * r["sebi_turnover_fee_pct"]
    stamp_duty = turnover * r["stamp_duty_buy_pct"] if side == "BUY" else 0.0
    brokerage = r["brokerage_per_order"]
    dp_charge = r["dp_charge_per_sell"] if side == "SELL" else 0.0
    gst = (brokerage + exchange_txn + dp_charge) * r["gst_pct"]
    total = stt + exchange_txn + sebi_fee + stamp_duty + brokerage + dp_charge + gst
    return {
        "stt": round(stt, 2), "exchange_txn": round(exchange_txn, 2),
        "sebi_fee": round(sebi_fee, 2), "stamp_duty": round(stamp_duty, 2),
        "brokerage": round(brokerage, 2), "dp_charge": round(dp_charge, 2),
        "gst": round(gst, 2), "total_charges": round(total, 2),
    }

def apply_slippage(price: float, side: str, slippage_bps: float = DEFAULT_SLIPPAGE_BPS) -> float:
    mult = 1 + slippage_bps / 10_000 if side == "BUY" else 1 - slippage_bps / 10_000
    return round(price * mult, 2)

def one_side_cost_rate(reference_turnover: float = 100_000.0,
                       slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
                       rates: dict | None = None) -> float:
    """Average one-side charge rate + slippage. Used by the sandbox."""
    buy = calculate_trade_charges(reference_turnover, "BUY", rates)["total_charges"] / reference_turnover
    sell = calculate_trade_charges(reference_turnover, "SELL", rates)["total_charges"] / reference_turnover
    return (buy + sell) / 2.0 + slippage_bps / 10_000.0
===END===

===FILE: backend/core/dsl.py===
"""Causal alpha DSL (ported verbatim from legacy alpha_sandbox/dsl.py).
AST-whitelisted expressions over OHLCV columns. No look-ahead by construction."""
import ast
import pandas as pd

COLUMNS = {"open", "high", "low", "close", "volume"}

def _sma(x, n): return x.rolling(int(n), min_periods=int(n)).mean()
def _ema(x, n): return x.ewm(span=int(n), min_periods=int(n)).mean()
def _std(x, n): return x.rolling(int(n), min_periods=int(n)).std()
def _delta(x, n): return x - x.shift(int(n))
def _zscore(x, n): return (x - _sma(x, n)) / _std(x, n).replace(0, pd.NA)
def _rank(x, n): return x.rolling(int(n), min_periods=int(n)).rank(pct=True)
def _rsi(x, n):
    n = int(n)
    d = x.diff()
    gain = d.clip(lower=0).ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - 100 / (1 + rs)
def _macd(x): return _ema(x, 12) - _ema(x, 26)
def _macd_hist(x): return _macd(x) - _ema(_macd(x), 9)
def _volume_ratio(x, n): return x / _sma(x, n).replace(0, pd.NA)

FUNCTIONS = {
    "sma": _sma, "ema": _ema, "std": _std, "delta": _delta,
    "zscore": _zscore, "rank": _rank, "rsi": _rsi,
    "macd": _macd, "macd_hist": _macd_hist, "volume_ratio": _volume_ratio,
    "abs": lambda x: x.abs(),
}
_ALLOWED_BIN = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.BitAnd, ast.BitOr)
_ALLOWED_UNARY = (ast.USub, ast.Invert)
_ALLOWED_CMP = (ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq)

class DSLValidationError(ValueError):
    """Raised when an alpha expression uses forbidden syntax."""

class _LogicToBitwise(ast.NodeTransformer):
    def visit_BoolOp(self, node):
        self.generic_visit(node)
        cls = ast.BitAnd if isinstance(node.op, ast.And) else ast.BitOr
        expr = node.values[0]
        for value in node.values[1:]:
            expr = ast.BinOp(left=expr, op=cls, right=value)
        return expr
    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not):
            return ast.UnaryOp(op=ast.Invert(), operand=node.operand)
        return node

def _check_node(node):
    if isinstance(node, (ast.Expression, ast.Load, *_ALLOWED_BIN, *_ALLOWED_UNARY, *_ALLOWED_CMP)):
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, bool)):
            raise DSLValidationError(f"forbidden constant: {node.value!r}")
        return
    if isinstance(node, ast.Name):
        if node.id not in COLUMNS and node.id not in FUNCTIONS:
            raise DSLValidationError(f"unknown name '{node.id}'")
        return
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BIN):
            raise DSLValidationError(f"forbidden operator: {type(node.op).__name__}")
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise DSLValidationError("forbidden unary operator")
        return
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if not isinstance(op, _ALLOWED_CMP):
                raise DSLValidationError(f"forbidden comparison: {type(op).__name__}")
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise DSLValidationError("only whitelisted function calls are allowed")
        if node.keywords:
            raise DSLValidationError("keyword arguments are not allowed")
        return
    raise DSLValidationError(f"forbidden syntax: {type(node).__name__}")

def _prepare(expr: str) -> ast.Expression:
    tree = _LogicToBitwise().visit(ast.parse(expr, mode="eval"))
    ast.fix_missing_locations(tree)
    for node in ast.walk(tree):
        _check_node(node)
    return tree

def validate_expression(expr: str):
    try:
        _prepare(expr)
    except SyntaxError as e:
        return False, f"syntax error: {e}"
    except DSLValidationError as e:
        return False, str(e)
    return True, None

def evaluate_expression(expr: str, df: pd.DataFrame) -> pd.Series:
    tree = _prepare(expr)
    env = {col: df[col].astype(float) for col in COLUMNS if col in df.columns}
    env.update(FUNCTIONS)
    code = compile(tree, "<alpha>", "eval")
    result = eval(code, {"__builtins__": {}}, env)
    if isinstance(result, (int, float, bool)):
        result = pd.Series(float(result), index=df.index)
    if not isinstance(result, pd.Series):
        raise DSLValidationError("expression must produce a series")
    return result.replace([float("inf"), float("-inf")], float("nan"))
===END===

===FILE: backend/core/gates.py===
"""Metrics + walk-forward + go/no-go gates (ported evaluator math, v2 itemized gates)."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252
GATE_SHARPE = 1.0
GATE_DD_RELATIVE = 0.75     # alpha maxDD must be <= 75% of benchmark maxDD
GATE_TURNOVER = 24.0        # hard gate, x/yr
TARGET_TURNOVER = 12.0      # generator target, x/yr
GATE_MIN_FOLD_WIN = 0.5     # fraction of folds with Sharpe > 0
DEFAULT_FOLDS = 3

def compute_metrics(daily_net: pd.Series) -> dict:
    d = daily_net.dropna()
    n = len(d)
    if n < 60:
        return {"days": n, "status": "insufficient_data"}
    growth = float((1.0 + d).prod())
    years = n / TRADING_DAYS
    ann_return = (growth ** (1.0 / years) - 1.0) if (years > 0 and growth > 0) else -1.0
    ann_vol = float(d.std(ddof=1)) * np.sqrt(TRADING_DAYS)
    sharpe = (ann_return / ann_vol) if ann_vol > 0 else 0.0
    cum = (1.0 + d).cumprod()
    max_dd = float((cum / cum.cummax() - 1.0).min())
    active = d[d != 0.0]
    win_rate = float((active > 0).mean()) if len(active) else 0.0
    return {
        "days": n, "ann_return_pct": round(ann_return * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2), "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd * 100, 2), "win_rate_pct": round(win_rate * 100, 1),
    }

def walk_forward_sharpes(daily_net: pd.Series, folds: int | None = None) -> list:
    folds = folds or DEFAULT_FOLDS
    d = daily_net.dropna()
    if len(d) < 60 * folds:
        return []
    return [float(round(compute_metrics(pd.Series(c)).get("sharpe", 0.0), 2))
            for c in np.array_split(d, folds)]

def apply_gates(metrics: dict, fold_sharpes: list, bench_max_dd_pct: float | None = None,
                benchmark_clone: bool = False, universe_ok: bool = True,
                turnover_gate: float = GATE_TURNOVER) -> dict:
    """Itemized gates. `all` is True only if EVERY single gate is True."""
    sharpe_ok = bool(metrics.get("sharpe", -9.0) >= GATE_SHARPE)
    dd_val = metrics.get("max_dd_pct", 999.0)
    if bench_max_dd_pct is None or bench_max_dd_pct >= 0:
        dd_ok = False
    else:
        dd_ok = bool(dd_val >= GATE_DD_RELATIVE * bench_max_dd_pct)
    stability_ok = bool(fold_sharpes and
                        sum(1 for s in fold_sharpes if s > 0) / len(fold_sharpes) >= GATE_MIN_FOLD_WIN)
    turnover_ok = bool(metrics.get("ann_turnover", 0.0) <= turnover_gate)
    clone_ok = not bool(benchmark_clone)
    universe_ok = bool(universe_ok)
    return {
        "sharpe": sharpe_ok, "max_dd": dd_ok, "stability": stability_ok,
        "turnover": turnover_ok, "clone": clone_ok, "universe": universe_ok,
        "all": sharpe_ok and dd_ok and stability_ok and turnover_ok and clone_ok and universe_ok,
    }
===END===

===FILE: backend/core/universe.py===
"""Qualified-universe predicates (pure). Data loading lives in the data layer (P2)."""
MIN_BARS = 1500
MIN_MEDIAN_TURNOVER = 5.0e7   # Rs 5 Cr, 20d median
MIN_PRICE = 50.0
PANEL_MULT = 4                # research panel >= 4x top_n

DELISTED_SYMBOLS = frozenset({
    "SWANENERGY.NS", "WELSPUNIND.NS", "SEQUENT.NS", "DPABOILERS.NS",
    "TCNSBRANDS.NS", "RAMAKRISHNA.NS", "AMAREJABAT.NS", "LTIM.NS",
    "L&TFH.NS", "SUVENPHAR.NS", "AEGISCHEM.NS", "PROCTER.NS",
    "ZOMATO.NS", "PEL.NS", "ISEC.NS", "MAINDREAL.NS",
    "NIPPONLIFE.NS", "VINATIORG.NS", "GMRINFRA.NS", "IBULHSGFIN.NS",
    "IDFC.NS", "PBFINTECH.NS", "CENTURYTEX.NS", "UNOINDA.NS",
    "MCDOWELL-N.NS", "NUVAM.NS",
})

def is_qualified(bars: int, median_turnover_20d: float, last_price: float) -> bool:
    return (bars >= MIN_BARS and median_turnover_20d >= MIN_MEDIAN_TURNOVER
            and last_price >= MIN_PRICE)

def panel_size_ok(panel_n: int, top_n: int) -> bool:
    """Mistake-ledger #6 guard: a tiny panel collapses every candidate into the
    same hold-all portfolio. Refuse instead of silently researching garbage."""
    return panel_n >= PANEL_MULT * top_n
===END===

===FILE: backend/core/regime.py===
"""Code-only regime ladder. Jev may only propose a bounded +/-1 step override."""
LADDER = ["CRISIS", "RISK_OFF", "NEUTRAL", "RISK_ON"]
CAPS = {"RISK_ON": 1.0, "NEUTRAL": 0.8, "RISK_OFF": 0.4, "CRISIS": 0.0}

def code_regime(breadth_pct: float, index_gap_pct: float, vol_percentile: float) -> dict:
    reasons = []
    if breadth_pct < 35:
        reasons.append(f"breadth {breadth_pct:.0f}% < 35%")
    if index_gap_pct < -6:
        reasons.append(f"index {index_gap_pct:.1f}% below 200d (>6%)")
    if vol_percentile > 90:
        reasons.append(f"vol pctile {vol_percentile:.0f} > 90")
    if reasons:
        state = "CRISIS"
    elif breadth_pct >= 60 and index_gap_pct >= 0:
        state = "RISK_ON"
    elif breadth_pct < 45 or index_gap_pct <= -3:
        state = "RISK_OFF"
    else:
        state = "NEUTRAL"
    return {"state": state, "cap": CAPS[state], "reasons": reasons}

def exposure_cap(state: str) -> float:
    return CAPS.get(state, 0.0)   # unknown state => fail-safe zero exposure

def apply_bounded_override(state: str, proposed: str) -> tuple:
    """Jev override: at most one ladder step. Returns (new_state, accepted)."""
    if proposed not in LADDER or state not in LADDER:
        return state, False
    if abs(LADDER.index(proposed) - LADDER.index(state)) <= 1:
        return proposed, True
    return state, False
===END===

===FILE: backend/core/portfolio.py===
"""Pure portfolio math: cadence, vol target, rank buffer, planner, budgets, stops."""
from datetime import date
import numpy as np
import pandas as pd

def should_rebalance(last_str: str | None, today_str: str, cadence_days: int) -> bool:
    if not last_str:
        return True
    try:
        last = date.fromisoformat(last_str)
        today = date.fromisoformat(today_str)
    except ValueError:
        return True
    calendar_cadence = cadence_days * 7 // 5
    return (today - last).days >= calendar_cadence

def vol_target_scale(realized_ann_vol: float, target_ann_vol: float,
                     min_exposure: float, max_exposure: float) -> float:
    if realized_ann_vol <= 1e-9:
        return max_exposure
    return max(min_exposure, min(max_exposure, target_ann_vol / realized_ann_vol))

def composite_index(close_df: pd.DataFrame) -> pd.Series:
    norm = close_df.div(close_df.iloc[0])
    return norm.mean(axis=1, skipna=True)

def trend_overlay_on(idx: pd.Series, window: int) -> bool:
    sma = idx.rolling(window, min_periods=window).mean()
    return bool(idx.iloc[-1] > sma.iloc[-1])

def realized_vol(daily: pd.Series, lookback: int = 120) -> float:
    d = daily.dropna().tail(lookback)
    if len(d) < 60:
        return 0.0
    return float(d.std(ddof=1) * np.sqrt(252))

def select_names(ranked: list, held: set, enter_n: int = 25, exit_n: int = 35) -> list:
    """Rank buffer: enter at rank <= enter_n, exit only past exit_n.
    ranked[0] is rank 1."""
    out = []
    for i, t in enumerate(ranked, start=1):
        if t in held:
            if i <= exit_n:
                out.append(t)
        elif i <= enter_n:
            out.append(t)
    return out

def plan_rebalance_orders(holdings: list, target_tickers: list, prices: dict,
                          total_value: float, exposure: float, top_n: int,
                          band: float = 0.10) -> dict:
    """Pure planner with +/-band no-trade zone (ported from meta_strategy)."""
    target_per = (total_value * exposure / top_n) if top_n else 0.0
    held = {h["ticker"]: h for h in holdings if h.get("quantity", 0) > 0}
    sells, buys, skipped = [], [], []
    for t, h in held.items():
        if t not in target_tickers:
            sells.append({"ticker": t, "quantity": h["quantity"], "reason": "exit"})
    for t in target_tickers:
        p = prices.get(t)
        if not p or p <= 0:
            skipped.append({"ticker": t, "reason": "no_price"})
            continue
        cur_qty = held.get(t, {}).get("quantity", 0)
        cur_val = cur_qty * p
        if cur_val < target_per * (1 - band):
            qty = int((target_per - cur_val) // p)
            if qty >= 1:
                buys.append({"ticker": t, "quantity": qty})
            else:
                skipped.append({"ticker": t, "reason": "qty_rounds_to_zero"})
        elif cur_val > target_per * (1 + band):
            qty = int((cur_val - target_per) // p)
            if qty >= 1:
                sells.append({"ticker": t, "quantity": min(qty, cur_qty), "reason": "trim"})
    return {"sells": sells, "buys": buys, "skipped": skipped,
            "target_per": round(target_per, 2)}

def apply_turnover_budget(orders: list, prices: dict, book_value: float,
                          cap_frac: float = 0.30) -> tuple:
    """Turnover budget <= cap_frac of book per rebalance; proportional scale-down."""
    turnover = sum(o["quantity"] * prices.get(o["ticker"], 0.0) for o in orders)
    cap = book_value * cap_frac
    if turnover <= cap or turnover <= 0:
        return orders, False
    scale = cap / turnover
    scaled = []
    for o in orders:
        q = int(o["quantity"] * scale)
        if q >= 1:
            scaled.append({**o, "quantity": q})
    return scaled, True

def catastrophic_stops(holdings: list, prices: dict, stop_pct: float = 0.25) -> list:
    """Name-level -25% from avg cost, checked at daily MTM, code-only."""
    out = []
    for h in holdings:
        t = h.get("ticker")
        avg = h.get("avg_price", 0)
        p = prices.get(t, 0)
        if avg > 0 and p > 0 and p <= avg * (1 - stop_pct):
            out.append(t)
    return out

def swap_worth_it(rank_old: int, rank_new: int, min_delta: int = 5) -> bool:
    """Cost-aware skip: rank delta < 5 => no swap."""
    return abs(rank_old - rank_new) >= min_delta
===END===

===FILE: backend/core/sleeves.py===
"""Sleeve signal definitions + pure weight helpers. Max 4 sleeves."""
import pandas as pd
from core.dsl import evaluate_expression

SLEEVE_SIGNALS = {
    "M": "close / sma(close, 200) - 1",          # trend_200 (validated, capital day-1)
    "D": "-(std(close, 120) / close)",           # low-vol defensive
    "R": "-(close / sma(close, 21) - 1)",        # rev_21 mean-reversion satellite
    "V": "(close / sma(close, 60) - 1) * volume_ratio(volume, 20)",  # volmom_60
}
MAX_SLEEVES = 4
GRADUATED_DAY1 = ["M"]

def sleeve_score(panel: dict, sleeve: str) -> dict:
    """Last-bar cross-sectional score per ticker. Higher = better long."""
    expr = SLEEVE_SIGNALS[sleeve]
    out = {}
    for ticker, df in panel.items():
        try:
            indexed = df.set_index("date").sort_index()
            s = evaluate_expression(expr, indexed).dropna()
            if len(s):
                out[ticker] = float(s.iloc[-1])
        except Exception:
            continue
    return out

def equal_weights(names: list) -> dict:
    if not names:
        return {}
    return {n: 1.0 / len(names) for n in names}

def inverse_vol_weights(vols: dict) -> dict:
    """Monthly reweight: trailing inverse-vol among graduated sleeves."""
    pos = {k: (1.0 / v) for k, v in vols.items() if v and v > 0}
    if not pos:
        return equal_weights(list(vols))
    total = sum(pos.values())
    return {k: v / total for k, v in pos.items()}
===END===

===FILE: backend/tests/conftest.py===
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
===END===

===FILE: backend/tests/test_core_costs_dsl.py===
import pandas as pd
from core.costs import calculate_trade_charges, apply_slippage, one_side_cost_rate
from core.dsl import validate_expression, evaluate_expression

def _df(n=120):
    prices = [100 + i * 0.5 for i in range(n)]
    return pd.DataFrame({"open": prices, "high": [p + 1 for p in prices],
                         "low": [p - 1 for p in prices], "close": prices,
                         "volume": [1_000_000] * n})

class TestCosts:
    def test_round_trip_70k(self):
        buy = calculate_trade_charges(70_000, "BUY")
        sell = calculate_trade_charges(70_000, "SELL")
        total = buy["total_charges"] + sell["total_charges"]
        assert 220.24 <= total <= 222.24
    def test_dp_zero_on_buy_once_on_sell(self):
        assert calculate_trade_charges(50_000, "BUY")["dp_charge"] == 0.0
        assert calculate_trade_charges(1_000, "SELL")["dp_charge"] == 15.0
        assert calculate_trade_charges(1_000_000, "SELL")["dp_charge"] == 15.0
    def test_gst_includes_dp(self):
        r = calculate_trade_charges(100_000, "SELL")
        expected = round((20.0 + 100_000 * 0.0000345 + 15.0) * 0.18, 2)
        assert abs(r["gst"] - expected) < 0.01
    def test_stamp_only_buy(self):
        assert calculate_trade_charges(100_000, "BUY")["stamp_duty"] > 0
        assert calculate_trade_charges(100_000, "SELL")["stamp_duty"] == 0.0
    def test_slippage_direction(self):
        assert apply_slippage(100.0, "BUY") == 100.40
        assert apply_slippage(100.0, "SELL") == 99.60
    def test_one_side_rate_positive(self):
        assert one_side_cost_rate() > 0

class TestDSL:
    def test_rejects_dangerous(self):
        for bad in ["__import__('os').system('ls')", "close.mean", "close[0]", "close + secret"]:
            ok, _ = validate_expression(bad)
            assert not ok
    def test_accepts_classic(self):
        ok, err = validate_expression("close / sma(close, 20) - 1")
        assert ok, err
    def test_momentum_sign_and_warmup(self):
        s = evaluate_expression("close / sma(close, 20) - 1", _df())
        assert s.iloc[-1] > 0
        assert s.iloc[:19].isna().all()
===END===

===FILE: backend/tests/test_core_gates_universe.py===
import numpy as np
import pandas as pd
from core.gates import compute_metrics, walk_forward_sharpes, apply_gates
from core.universe import is_qualified, panel_size_ok

def test_metrics_basic():
    assert compute_metrics(pd.Series([0.0] * 100))["sharpe"] == 0.0
    rng = np.random.RandomState(42)
    assert compute_metrics(pd.Series(rng.normal(0.001, 0.01, 300)))["sharpe"] > 0
    m = compute_metrics(pd.Series([0.0] * 60 + [0.10, -0.05, 0.0]))
    assert -5.1 <= m["max_dd_pct"] <= -4.9

def test_gates_all_pass():
    g = apply_gates({"sharpe": 1.2, "max_dd_pct": -20, "ann_turnover": 10},
                    [1.0, 0.8, 1.1], bench_max_dd_pct=-40)
    assert g["all"] and g["sharpe"] and g["max_dd"] and g["stability"] and g["turnover"]

def test_each_gate_can_kill_all():
    base = {"sharpe": 1.2, "max_dd_pct": -20, "ann_turnover": 10}
    folds = [1.0, 0.8, 1.1]
    assert apply_gates({**base, "sharpe": 0.9}, folds, -40)["all"] is False
    assert apply_gates({**base, "max_dd_pct": -31}, folds, -40)["all"] is False
    assert apply_gates({**base, "ann_turnover": 25}, folds, -40)["all"] is False
    assert apply_gates(base, [-0.5, -0.2, 0.4], -40)["all"] is False
    assert apply_gates(base, folds, -40, benchmark_clone=True)["all"] is False
    assert apply_gates(base, folds, -40, universe_ok=False)["all"] is False
    assert apply_gates(base, folds, None)["all"] is False   # missing benchmark => dd gate fails

def test_universe_rules():
    assert is_qualified(1500, 5.0e7, 50.0)
    assert not is_qualified(1499, 5.0e7, 50.0)
    assert not is_qualified(1500, 4.9e7, 50.0)
    assert not is_qualified(1500, 5.0e7, 49.0)
    assert panel_size_ok(100, 25)
    assert not panel_size_ok(99, 25)
===END===

===FILE: backend/tests/test_core_portfolio_sleeves.py===
import pandas as pd
import pytest
from core.portfolio import (should_rebalance, vol_target_scale, select_names,
                            plan_rebalance_orders, apply_turnover_budget,
                            catastrophic_stops, swap_worth_it, composite_index,
                            trend_overlay_on)
from core.sleeves import sleeve_score, inverse_vol_weights, equal_weights, SLEEVE_SIGNALS

def test_cadence_and_vol_scale():
    assert should_rebalance(None, "2026-10-15", 60) is True
    assert should_rebalance("2026-09-01", "2026-10-15", 60) is False
    assert should_rebalance("2026-06-01", "2026-10-15", 60) is True
    assert vol_target_scale(0.10, 0.15, 0.25, 1.0) == 1.0
    assert vol_target_scale(0.30, 0.15, 0.25, 1.0) == pytest.approx(0.5)
    assert vol_target_scale(0.90, 0.15, 0.25, 1.0) == 0.25

def test_rank_buffer():
    ranked = [f"T{i}" for i in range(1, 41)]
    assert select_names(ranked, set(), 25, 35) == ranked[:25]
    assert "T30" in select_names(ranked, {"T30"}, 25, 35)      # held inside buffer stays
    assert "T36" not in select_names(ranked, {"T36"}, 25, 35)   # held past exit rank drops

def test_planner_band_and_exits():
    plan = plan_rebalance_orders([{"ticker": "A.NS", "quantity": 340}], ["A.NS"],
                                 {"A.NS": 100.0}, 100_000, 0.7, 2)
    assert plan["buys"] == [] and not plan["sells"]             # inside +/-10% band
    plan = plan_rebalance_orders([{"ticker": "OLD.NS", "quantity": 10}], ["A.NS"],
                                 {"OLD.NS": 10.0, "A.NS": 100.0}, 100_000, 0.7, 1)
    assert any(s["reason"] == "exit" for s in plan["sells"])

def test_turnover_budget_scales():
    orders = [{"ticker": "A", "quantity": 400, "side": "BUY"}]
    scaled, hit = apply_turnover_budget(orders, {"A": 100.0}, 100_000, 0.30)
    assert hit and scaled[0]["quantity"] <= 300
    small = [{"ticker": "A", "quantity": 100, "side": "BUY"}]
    kept, hit2 = apply_turnover_budget(small, {"A": 100.0}, 100_000, 0.30)
    assert not hit2 and kept == small

def test_catastrophic_stop_and_swap_skip():
    h = [{"ticker": "X", "avg_price": 100.0}]
    assert catastrophic_stops(h, {"X": 74.0}) == ["X"]
    assert catastrophic_stops(h, {"X": 76.0}) == []
    assert swap_worth_it(30, 24) is True
    assert swap_worth_it(30, 26) is False

def test_weights():
    w = inverse_vol_weights({"a": 0.10, "b": 0.30})
    assert w["a"] == pytest.approx(0.75) and abs(sum(w.values()) - 1.0) < 1e-9
    assert equal_weights(["a", "b"]) == {"a": 0.5, "b": 0.5}

def test_sleeve_m_on_rising_panel():
    dates = pd.date_range("2023-01-01", periods=300, freq="B")
    panel = {}
    for i, drift in enumerate([0.10, 0.05, -0.05]):
        close = 100 + drift * np.arange(300) if False else [100 + drift * t for t in range(300)]
        panel[f"T{i}.NS"] = pd.DataFrame({"date": dates, "open": close, "high": close,
                                          "low": close, "close": close,
                                          "volume": [1_000_000] * 300})
    scores = sleeve_score(panel, "M")
    assert scores["T0.NS"] > scores["T2.NS"]
    assert all(SLEEVE_SIGNALS[k] for k in ("M", "D", "R", "V"))

def test_trend_overlay():
    up = pd.Series([100 + i for i in range(250)])
    assert trend_overlay_on(up, 200) is True
    down = pd.Series([200 - i for i in range(250)])
    assert trend_overlay_on(down, 200) is False
    assert composite_index(pd.DataFrame({"a": up, "b": up})).iloc[-1] == pytest.approx(3.49)
===END===

===FILE: backend/tests/test_core_regime_ist.py===
import re
from core.regime import code_regime, apply_bounded_override, exposure_cap
from core.ist import ist_today_str, is_stale_day_open

def test_crisis_triggers():
    assert code_regime(34, 1.0, 40)["state"] == "CRISIS"
    assert code_regime(60, -7.0, 40)["state"] == "CRISIS"
    assert code_regime(60, 1.0, 91)["state"] == "CRISIS"
    assert exposure_cap("CRISIS") == 0.0

def test_ladder():
    assert code_regime(70, 2.0, 40)["state"] == "RISK_ON"
    assert code_regime(50, -1.0, 50)["state"] == "NEUTRAL"
    assert code_regime(40, -1.0, 50)["state"] == "RISK_OFF"
    assert code_regime(50, -4.0, 50)["state"] == "RISK_OFF"
    assert exposure_cap("NEUTRAL") == 0.8

def test_bounded_override():
    assert apply_bounded_override("NEUTRAL", "RISK_ON") == ("RISK_ON", True)
    assert apply_bounded_override("NEUTRAL", "CRISIS") == ("NEUTRAL", False)
    assert apply_bounded_override("RISK_ON", "RISK_OFF") == ("RISK_ON", False)
    assert apply_bounded_override("NEUTRAL", "GARBAGE") == ("NEUTRAL", False)

def test_ist_helpers():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ist_today_str())
    assert is_stale_day_open("2026-10-14", "2026-10-15") is True
    assert is_stale_day_open("2026-10-15", "2026-10-15") is False
    assert is_stale_day_open(None, "2026-10-15") is True
===END===
