"""
Research trigger endpoints (Section 3 amendment) — manual scoped evolution.

POST /api/research/trigger?count=N  → starts a background batch (async),
                                      returns run_id immediately.
GET  /api/research/status[?run_id=] → status + summary log of current/latest run.

The batch NEVER writes to the meta book or legacy book — only alpha_registry
and hall_of_fame (enforced inside evolution_driver / generate_alphas).
"""
import logging

from fastapi import APIRouter

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["Research"])


@router.post("/trigger")
async def research_trigger(count: int | None = None):
    """
    Start one scoped research batch in the background.
    count is clamped to [1, settings.evolution_max_candidates] (hard cap 6).
    Returns {run_id, status: started} immediately; poll GET /api/research/status.
    """
    from evolution_driver import start_batch

    result = start_batch(count)
    if result.get("status") == "started":
        logger.info(f"Research batch triggered via API: {result}")
    return result


@router.get("/status")
async def research_status(run_id: str | None = None):
    """Status + summary log of the given (or current/latest) research run."""
    from evolution_driver import get_run_status

    snap = get_run_status(run_id)
    if not snap:
        return {"status": "no_runs_yet"}
    tail = snap.get("log_tail")
    snap["log_tail"] = list(tail) if hasattr(tail, "__iter__") else []
    return snap
