"""
Hall of Fame — promotion layer over alpha_registry (Phase 4).
Promotion requires stored gates: sharpe passed + stability passed +
drawdown passed (absolute, or relative to the benchmark DD recorded at
test time in info.bench_max_dd_pct). Offline only.
Runnable: python -m alpha_sandbox.hall_of_fame
"""
import asyncio
import logging
from datetime import datetime, timezone

from config import settings
from database import connect_db, close_db, get_db

logger = logging.getLogger(__name__)
COLLECTION = "hall_of_fame"


def is_promotable(doc: dict, dd_mode: str = "relative",
                  dd_relative: float = 0.75,
                  abs_dd_pct: float = 25.0) -> bool:
    """Pure promotion predicate (unit-testable, no DB)."""
    gates = doc.get("gates") or {}
    metrics = doc.get("metrics") or {}

    # 1. HARD GATE: Must pass all composite gates (rejects benchmark clones)
    if not gates.get("all", False):
        return False

    # 2. HARD GATE: Explicitly reject benchmark clones (belt-and-braces)
    if metrics.get("benchmark_clone", False):
        return False

    # 3. Existing logic (Sharpe, Stability, Drawdown)
    if not gates.get("sharpe") or not gates.get("stability"):
        return False

    alpha_dd = metrics.get("max_dd_pct")
    if alpha_dd is None:
        return False

    mode = dd_mode or getattr(settings, "alpha_gate_dd_mode", "relative")
    if mode == "relative":
        bench_dd = (doc.get("info") or {}).get("bench_max_dd_pct")
        if bench_dd is None or bench_dd >= 0:
            return False
        rel = dd_relative if dd_relative is not None else getattr(settings, "alpha_gate_dd_relative", 0.75)
        threshold = bench_dd * rel
        return alpha_dd >= threshold  # e.g., -30 >= -40 * 0.75
    else:
        limit = (abs_dd_pct if abs_dd_pct is not None else getattr(settings, "alpha_gate_max_dd", 0.25) * 100)
        return alpha_dd >= -abs(limit)


async def refresh_hall_of_fame() -> dict:
    reg = get_db()["alpha_registry"]
    hof = get_db()[COLLECTION]
    newly_promoted_count = 0
    async for doc in reg.find({}):
        if not is_promotable(doc):
            continue
        expr = doc.get("expression")
        name = doc.get("name")
        query = {"expression": expr} if expr else {"name": name}
        already_active = await hof.find_one({
            **query,
            "$or": [{"active": True}, {"status": "active"}],
        })
        await hof.update_one(
            query,
            {"$set": {
                "name": name,
                "expression": expr,
                "source": doc.get("source"),
                "metrics": doc.get("metrics"),
                "fold_sharpes": doc.get("fold_sharpes"),
                "promoted_at": datetime.now(timezone.utc),
                "status": "active",
                "active": True,
            }},
            upsert=True,
        )
        if not already_active:
            newly_promoted_count += 1

    active_count = await hof.count_documents({"$or": [{"active": True}, {"status": "active"}]})
    logger.info(f"Hall of Fame refresh: newly_promoted={newly_promoted_count} active={active_count}")
    return {
        "active": active_count,
        "promoted": newly_promoted_count,
        "total_historical": active_count,
    }


async def list_hall_of_fame(limit: int = 50) -> list:
    cursor = (get_db()[COLLECTION]
              .find({"$or": [{"active": True}, {"status": "active"}]}, {"_id": 0})
              .sort("promoted_at", -1).limit(limit))
    return await cursor.to_list(length=limit)


async def demote(expression: str, reason: str) -> dict:
    res = await get_db()[COLLECTION].update_one(
        {"expression": expression, "$or": [{"active": True}, {"status": "active"}]},
        {"$set": {"status": "demoted",
                  "active": False,
                  "demoted_at": datetime.now(timezone.utc),
                  "demote_reason": reason}},
    )
    return {"demoted": res.modified_count}


async def _main():
    await connect_db()
    out = await refresh_hall_of_fame()
    print(f"Hall of Fame: {out}")
    for h in await list_hall_of_fame():
        m = h.get("metrics") or {}
        print(f"  {h.get('name')}: sharpe={m.get('sharpe')} "
              f"maxDD={m.get('max_dd_pct')}% | {h.get('expression')}")
    await close_db()


if __name__ == "__main__":
    asyncio.run(_main())
