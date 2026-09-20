"""
Research trigger endpoints (Section 3 amendment) — manual scoped evolution.

POST /api/research/trigger?count=N  → starts a background batch (async),
                                      returns run_id immediately.
GET  /api/research/status[?run_id=] → status + summary log of current/latest run.

The batch NEVER writes to the meta book or legacy book — only alpha_registry
and hall_of_fame (enforced inside evolution_driver / generate_alphas).
"""
from datetime import datetime, timezone
import logging

from fastapi import APIRouter, HTTPException

from alpha_sandbox.hall_of_fame import is_promotable
from config import settings
from database import get_db

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
    if "candidates" not in snap:
        snap["candidates"] = []
    return snap


@router.post("/hof/revalidate")
async def revalidate_hall_of_fame():
    """
    Scans the active Hall of Fame. Demotes any entries that no longer 
    pass the strict is_promotable() checks (e.g., benchmark clones).
    """
    db = get_db()
    if db is None:
        from database import ensure_connected
        if not await ensure_connected():
            raise HTTPException(status_code=503, detail="Database offline")
        db = get_db()

    hof_coll = db["hall_of_fame"]
    reg_coll = db["alpha_registry"]

    active_members = await hof_coll.find(
        {"$or": [{"active": True}, {"status": "active"}]}
    ).to_list(length=1000)
    scanned = len(active_members)
    demoted = 0

    for member in active_members:
        # Fetch original registry doc to re-evaluate gates
        reg_doc = await reg_coll.find_one({"name": member.get("name")})
        if not reg_doc and member.get("expression"):
            reg_doc = await reg_coll.find_one({"expression": member.get("expression")})

        if not reg_doc:
            # Orphaned HoF entry
            await hof_coll.update_one(
                {"_id": member["_id"]},
                {"$set": {
                    "active": False,
                    "status": "demoted",
                    "demoted_at": datetime.now(timezone.utc),
                    "demoted_reason": "orphaned",
                }}
            )
            demoted += 1
            continue

        # Re-run strict promotion logic
        # Note: Pass default DD settings or fetch from config
        if not is_promotable(reg_doc, dd_mode="relative", dd_relative=0.75):
            await hof_coll.update_one(
                {"_id": member["_id"]},
                {"$set": {
                    "active": False,
                    "status": "demoted",
                    "demoted_at": datetime.now(timezone.utc),
                    "demoted_reason": "failed strict revalidation (clone or gates.all=False)",
                }}
            )
            demoted += 1

    remaining = scanned - demoted
    logger.info(f"HoF revalidation: scanned={scanned}, demoted={demoted}, remaining={remaining}")
    return {
        "status": "success",
        "scanned": scanned,
        "demoted": demoted,
        "remaining_active": remaining,
    }

