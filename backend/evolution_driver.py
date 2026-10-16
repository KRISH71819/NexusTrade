"""
Scoped Alpha Evolution Driver (V2 Quantitative Pipeline).

Runs the offline research pipeline (core.template_bank -> core.critics ->
alpha_sandbox -> hall_of_fame) inside hard limits:
  - max settings.evolution_max_candidates candidates per batch
  - wall-clock cap of settings.evolution_time_cap_min minutes
  - universe: DEFAULT_TICKERS (12 liquid majors)
  - zero LLM hallucinations; pure math expressions with Jev adversarial screening
  - writes ONLY to alpha_registry / hall_of_fame collections — NEVER to the
    meta book or the legacy book

Isolation: Runs in a worker thread with its own event loop via
asyncio.to_thread + asyncio.run, so the main loop (risk checks, meta jobs,
frontend broadcast) stays responsive even if a manual trigger lands during market hours.
"""
import asyncio
import logging
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# ── In-process run registry (one batch at a time) ────────────────────────────
_runs: dict = {}
_current_run_id: Optional[str] = None
_runs_guard = threading.Lock()
_LOG_TAIL_LEN = 40


def _log_line(record: dict, message: str) -> None:
    """Append to the run's log tail (capped) and mirror to app log."""
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} | {message}"
    tail = record.get("log_tail")
    if isinstance(tail, deque):
        tail.append(line)
    logger.info(f"[EVO:{record.get('run_id', '?')}] {message}")


def get_run_status(run_id: str | None = None) -> dict:
    """
    Status snapshot for GET /api/research/status.
    Returns the given run, else the current/latest run; {} when none exist.
    """
    with _runs_guard:
        rec = _runs.get(run_id) if run_id else None
        if rec is None and not run_id:
            if _current_run_id and _current_run_id in _runs:
                rec = _runs[_current_run_id]
            elif _runs:
                rec = max(_runs.values(), key=lambda r: r.get("started_ts", ""))
        if not rec:
            return {}
        snap = dict(rec)
        if "candidates" in snap:
            snap["candidates"] = list(snap["candidates"])
        return snap


def is_running() -> bool:
    """True while a scoped batch is executing."""
    with _runs_guard:
        return bool(_current_run_id)


# ═══════════════════════════════ BATCH CORE ══════════════════════════════════

DEFAULT_TICKERS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "TATAMOTORS.NS", "SUNPHARMA.NS", "LT.NS", "SBIN.NS", "BAJFINANCE.NS",
    "HINDALCO.NS", "MARUTI.NS",
]
DEFAULT_START = "2010-01-01"


async def _batch_coroutine(count: int, deadline_mono: float, record: dict) -> dict:
    """
    The V2 quantitative research pipeline. Runs inside the worker thread's
    own event loop. Uses core.template_bank expressions, screens with structural
    and Jev critics, backtests in sandbox, and promotes to Hall of Fame.
    """
    from database import connect_db, close_db, get_db
    from alpha_sandbox.sandbox import load_history_panel, backtest_signal
    from alpha_sandbox.evaluator import compute_metrics, walk_forward_sharpes, apply_gates
    from alpha_sandbox import registry, hall_of_fame
    from core.template_bank import generate_candidate_expressions
    from core.critics import check_structural_rejections, jev_critic_question, parse_critic_answer
    from core.jev_adapter import JevAdapter

    await connect_db()
    try:
        start_dt = datetime.fromisoformat(DEFAULT_START).replace(tzinfo=timezone.utc)
        panel = await load_history_panel(DEFAULT_TICKERS, start=start_dt)
        if not panel:
            _log_line(record, "no usable OHLCV history — run fetch_history.py first")
            return {"status": "no_history", "proposed": 0, "tested": 0}

        # Benchmark: equal-weight buy&hold over the same panel/window.
        bench_daily, _bench_info = backtest_signal(panel, "close / close")
        bench = compute_metrics(bench_daily)
        bench_dd = float(bench.get("max_dd_pct") or -40.0)
        _log_line(record,
                  f"benchmark buy&hold ({DEFAULT_START}+): ann {bench.get('ann_return_pct')}% "
                  f"sharpe {bench.get('sharpe')} maxDD {bench_dd}%")

        jev = JevAdapter()
        all_cands = generate_candidate_expressions()
        cands = all_cands[:count]
        proposed = len(cands)
        record["proposed"] = proposed
        record["candidates"] = []
        _log_line(record, f"V2 template bank proposed {proposed} candidate(s)")

        tested = 0
        for cand in cands:
            # Cooperative deadline check between candidates.
            if time.monotonic() > deadline_mono:
                _log_line(record, f"time cap ({settings.evolution_time_cap_min} min) reached "
                                  f"— stopping after {tested} tested")
                break

            expr = cand.get("expression", "")
            name = cand.get("name", "v2_alpha")
            hypothesis = cand.get("hypothesis", "")

            # 1. Structural Critic (Free, instant)
            flaw = check_structural_rejections(expr)
            if flaw:
                _log_line(record, f"candidate '{name}': rejected by structural critic ({flaw})")
                tested += 1
                record["tested"] = tested
                record["candidates"].append({
                    "name": name,
                    "hypothesis": hypothesis,
                    "expression": expr,
                    "metrics": {
                        "sharpe": None, "max_dd_pct": None, "ann_turnover": None,
                        "fold_sharpes": [], "ann_return_pct": None, "ann_vol_pct": None,
                    },
                    "gates": {"sharpe": False, "max_dd": False, "stability": False, "turnover": False, "all": False},
                    "verdict": "REJECT",
                    "reject_reason": flaw,
                })
                continue

            # 2. Jev Adversarial Critic (fail-closed if explicit reject)
            try:
                q = jev_critic_question(expr, hypothesis)
                critic_ans = await jev.query(state=expr, questions=q)
                verdict = parse_critic_answer(critic_ans)
                if verdict.get("verdict") == "REJECT":
                    reason = verdict.get("fatal_flaw") or "Rejected by Jev critic"
                    _log_line(record, f"candidate '{name}': rejected by Jev critic ({reason})")
                    tested += 1
                    record["tested"] = tested
                    record["candidates"].append({
                        "name": name,
                        "hypothesis": hypothesis,
                        "expression": expr,
                        "metrics": {
                            "sharpe": None, "max_dd_pct": None, "ann_turnover": None,
                            "fold_sharpes": [], "ann_return_pct": None, "ann_vol_pct": None,
                        },
                        "gates": {"sharpe": False, "max_dd": False, "stability": False, "turnover": False, "all": False},
                        "verdict": "REJECT",
                        "reject_reason": reason,
                    })
                    continue
            except Exception as e:
                _log_line(record, f"candidate '{name}': Jev critic query skipped ({e})")

            # 3. Pass to Sandbox & Gates
            try:
                portfolio_rule = cand.get("portfolio_rule", getattr(settings, "alpha_default_portfolio_rule", "top_n"))
                daily_net, info = backtest_signal(
                    panel, expr,
                    portfolio_rule=portfolio_rule,
                    top_n=settings.meta_top_n,
                )
                met = compute_metrics(daily_net)
                folds = walk_forward_sharpes(daily_net, n_splits=3)
                gates = apply_gates(met, folds, bench_dd)
                verdict = "APPROVE" if gates.get("all") else "REJECT"

                # Persist to registry
                await registry.save_alpha_result(
                    expr, f"v2_{name}",
                    {"hypothesis": hypothesis, "bench_max_dd_pct": bench_dd},
                    met, folds, gates, source="v2_template_bank",
                )

                # Check Hall of Fame promotion
                if gates.get("all") and hall_of_fame.is_promotable({
                    "name": f"v2_{name}", "expression": expr, "metrics": met, "gates": gates, "fold_sharpes": folds,
                }):
                    await hall_of_fame.promote_to_hall_of_fame(
                        name=f"v2_{name}",
                        expression=expr,
                        hypothesis=hypothesis,
                        metrics=met,
                        source="v2_template_bank",
                    )
                    _log_line(record, f"PROMOTED candidate '{name}' to Hall of Fame!")

                tested += 1
                record["tested"] = tested
                cand_res = {
                    "name": name,
                    "hypothesis": hypothesis,
                    "expression": expr,
                    "metrics": met,
                    "gates": gates,
                    "verdict": verdict,
                }
                record["candidates"].append(cand_res)
                sh = met.get("sharpe")
                _log_line(record, f"candidate '{name}': sharpe={sh} maxDD={met.get('max_dd_pct')}% turnover={met.get('ann_turnover')}x/yr verdict={verdict}")
            except Exception as e:
                tested += 1
                record["tested"] = tested
                record["candidates"].append({
                    "name": name,
                    "hypothesis": hypothesis,
                    "expression": expr,
                    "metrics": {
                        "sharpe": None, "max_dd_pct": None, "ann_turnover": None,
                        "fold_sharpes": [], "ann_return_pct": None, "ann_vol_pct": None,
                    },
                    "gates": {"sharpe": False, "max_dd": False, "stability": False, "turnover": False, "all": False},
                    "verdict": "REJECT",
                    "error": str(e),
                })
                _log_line(record, f"candidate '{name}' backtest errored: {e}")

        # Gate-pass tally from the registry (docs written during THIS batch).
        batch_started_utc = datetime.fromtimestamp(
            record["started_ts_epoch"], tz=timezone.utc
        )
        coll = get_db()["alpha_registry"]
        passed = await coll.count_documents({
            "created_at": {"$gte": batch_started_utc},
            "gates.all": True,
        })
        record["passed"] = int(passed)

        hof = await hall_of_fame.refresh_hall_of_fame()
        # FIX: Use the delta ('promoted') instead of the cumulative total
        record["hof_promoted"] = int(hof.get("promoted", 0))
        record["hof_active"] = int(hof.get("active", 0))
        _log_line(record, f"hall_of_fame refresh: promoted={hof.get('promoted')} "
                          f"active={hof.get('active')}")

        return {
            "status": "completed",
            "proposed": proposed,
            "tested": tested,
            "passed": record["passed"],
            "hof_promoted": record["hof_promoted"],
            "hof_active": record["hof_active"],
            "candidates": record["candidates"],
        }
    finally:
        await close_db()


def _batch_sync(count: int, deadline_mono: float, record: dict) -> dict:
    """Worker-thread entry: dedicated event loop for the isolated batch."""
    return asyncio.run(_batch_coroutine(count, deadline_mono, record))


# ═════════════════════════ PUBLIC ENTRYPOINT ═════════════════════════════════

_background_tasks: set = set()  # strong refs so created tasks are never GC'd


def start_batch(count: Optional[int] = None) -> dict:
    """
    Fire-and-forget starter for API callers (async-start + poll pattern).
    Returns immediately with {'status': 'started', 'run_id', ...} or
    {'status': 'already_running', 'run_id'}. Must be called from async context.
    """
    if is_running():
        with _runs_guard:
            rid = _current_run_id
        current = get_run_status(rid)
        return {"status": "already_running",
                "run_id": rid,
                "started_at": current.get("started_at")}

    cap = max(1, int(settings.evolution_max_candidates))
    clamped = max(1, min(cap, int(count))) if count is not None else cap
    rid = uuid.uuid4().hex[:12]

    task = asyncio.create_task(run_scoped_evolution(count=clamped, run_id=rid))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info(f"[EVO:{rid}] scoped batch queued via start_batch "
                f"(count={clamped}, cap={settings.evolution_time_cap_min} min)")
    return {
        "status": "started",
        "run_id": rid,
        "count": clamped,
        "time_cap_min": settings.evolution_time_cap_min,
    }


async def run_scoped_evolution(count: Optional[int] = None,
                               run_id: Optional[str] = None) -> dict:
    """
    Run one scoped research batch. Shared by the Saturday cron job and the
    manual POST /api/research/trigger endpoint.

    Never raises. Concurrent invocation returns already_running immediately.
    Returns a summary dict (also stored retrievable via get_run_status()).
    """
    global _current_run_id

    cap = max(1, int(settings.evolution_max_candidates))
    requested = cap if count is None else max(1, min(cap, int(count)))
    time_cap_s = max(1.0, float(settings.evolution_time_cap_min)) * 60.0

    with _runs_guard:
        if _current_run_id:
            return {"status": "already_running", "run_id": _current_run_id}
        rid = run_id or uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        record = {
            "run_id": rid,
            "status": "running",
            "started_at": now.isoformat(),
            "started_ts": now.isoformat(),
            "started_ts_epoch": time.time(),
            "finished_at": None,
            "count_requested": requested,
            "proposed": 0,
            "tested": 0,
            "passed": 0,
            "hof_promoted": 0,
            "hof_active": 0,
            "elapsed_s": None,
            "error": None,
            "candidates": [],
            "log_tail": deque(maxlen=_LOG_TAIL_LEN),
        }
        _runs[rid] = record
        _current_run_id = rid

    started_mono = time.monotonic()
    deadline = started_mono + time_cap_s
    _log_line(record, f"scoped batch starting: count={requested}, "
                      f"time_cap={settings.evolution_time_cap_min} min")

    try:
        # Outer wait_for = belt-and-braces around the cooperative deadline.
        summary = await asyncio.wait_for(
            asyncio.to_thread(_batch_sync, requested, deadline, record),
            timeout=time_cap_s + 30.0,  # small grace for cleanup/close
        )
        record["status"] = summary.get("status", "completed")
        for k in ("proposed", "tested", "passed", "hof_promoted", "hof_active", "candidates"):
            if k in summary:
                record[k] = summary[k]
        return {"run_id": rid, **summary}
    except asyncio.TimeoutError:
        record["status"] = "timeout"
        _log_line(record, "outer wall-clock cap hit — batch abandoned (worker "
                          "thread will exit at its next cooperative check)")
        return {"run_id": rid, "status": "timeout",
                "tested": record["tested"], "passed": record["passed"]}
    except Exception as e:  # never raise into caller (cron safety)
        record["status"] = "error"
        record["error"] = str(e)[:300]
        _log_line(record, f"batch failed: {e}")
        logger.error(f"[EVO:{rid}] unexpected failure", exc_info=True)
        return {"run_id": rid, "status": "error", "error": str(e)[:300]}
    finally:
        elapsed = round(time.monotonic() - started_mono, 1)
        record["elapsed_s"] = elapsed
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        with _runs_guard:
            if _current_run_id == rid:
                _current_run_id = None
        logger.info(f"[EVO:{rid}] batch finished status={record['status']} "
                    f"tested={record['tested']} passed={record['passed']} "
                    f"in {elapsed}s")
