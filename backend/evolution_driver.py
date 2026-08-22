"""
Scoped Alpha Evolution Driver (Section 3).

Runs the offline research pipeline (alpha_generator -> alpha_critic ->
alpha_sandbox -> hall_of_fame) inside hard limits:
  - max settings.evolution_max_candidates candidates per batch
  - wall-clock cap of settings.evolution_time_cap_min minutes
  - universe: generate_alphas.DEFAULT_TICKERS (12 liquid majors)
  - model order: generator's own compound-first fallback chain (unchanged)
  - writes ONLY to alpha_registry / hall_of_fame collections — NEVER to the
    meta book or the legacy book

Thread isolation matters: alpha_generator / alpha_critic make BLOCKING sync
LLM calls (with time.sleep rate-limit spacing). The batch therefore runs in a
worker thread with its own event loop via asyncio.to_thread + asyncio.run, so
the main loop (risk checks, meta jobs, frontend broadcast) stays responsive
even if a manual trigger lands during market hours.

Timeout model: cooperative deadline checked between candidates; the outer
asyncio.wait_for is belt-and-braces (a thread cannot be force-killed — if the
inner deadline check is somehow missed, the abandoned result is logged and
the run is marked timeout).

Import-safe: heavy imports happen inside functions; importing this module
performs no network or DB work.
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
        return dict(rec) if rec else {}


def is_running() -> bool:
    """True while a scoped batch is executing."""
    with _runs_guard:
        return bool(_current_run_id)


# ═══════════════════════════════ BATCH CORE ══════════════════════════════════

async def _batch_coroutine(count: int, deadline_mono: float, record: dict) -> dict:
    """
    The actual research pipeline. Runs inside the worker thread's own event
    loop (see run_scoped_evolution). Reuses generate_alphas.process_candidate
    unchanged, whose only DB writes are alpha_registry inserts.
    """
    from database import connect_db, close_db, get_db
    from alpha_sandbox.sandbox import load_history_panel, backtest_signal
    from alpha_sandbox.evaluator import compute_metrics
    from alpha_sandbox import registry, hall_of_fame
    from alpha_generator import generate_candidates
    from generate_alphas import DEFAULT_TICKERS, DEFAULT_START, process_candidate

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

        memory = await registry.list_alphas(limit=12)
        cands = generate_candidates(count, memory)  # blocking LLM call (isolated thread)
        proposed = len(cands)
        record["proposed"] = proposed
        _log_line(record, f"generator proposed {proposed} candidate(s)")

        tested = 0
        for cand in cands:
            # Cooperative deadline check between candidates.
            if time.monotonic() > deadline_mono:
                _log_line(record, f"time cap ({settings.evolution_time_cap_min} min) reached "
                                  f"— stopping after {tested} tested")
                break
            name = cand.get("name", "?")
            try:
                metrics = await process_candidate(cand, panel, bench_dd)
                tested += 1
                record["tested"] = tested
                if metrics:
                    _log_line(record,
                              f"candidate '{name}': sharpe={metrics.get('sharpe')} "
                              f"maxDD={metrics.get('max_dd_pct')}% "
                              f"turnover={metrics.get('ann_turnover')}x/yr")
                else:
                    _log_line(record, f"candidate '{name}': rejected pre/post-backtest")
            except Exception as e:  # one bad candidate must never kill the batch
                tested += 1
                record["tested"] = tested
                _log_line(record, f"candidate '{name}' errored: {e}")

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
        for k in ("proposed", "tested", "passed", "hof_promoted", "hof_active"):
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
