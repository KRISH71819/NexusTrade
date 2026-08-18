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


def is_promotable(doc: dict, dd_mode: str | None = None,
                  dd_relative: float | None = None,
                  abs_dd_pct: float | None = None) -> bool:
    """Pure promotion predicate (unit-testable, no DB)."""
    dd_mode = dd_mode or settings.alpha_gate_dd_mode
    gates = doc.get("gates") or {}
    if not gates.get("sharpe") or not gates.get("stability"):
        return False
    metrics = doc.get("metrics") or {}
    alpha_dd = metrics.get("max_dd_pct")
    if alpha_dd is None:
        return False
    if dd_mode == "absolute":
        limit = (abs_dd_pct if abs_dd_pct is not None else settings.alpha_gate_max_dd * 100)
        return alpha_dd >= -limit
    rel = dd_relative if dd_relative is not None else settings.alpha_gate_dd_relative
    bench_dd = (doc.get("info") or {}).get("bench_max_dd_pct")
    if bench_dd is None or bench_dd >= 0:
        return False
    return alpha_dd >= rel * bench_dd


async def refresh_hall_of_fame() -> dict:
    reg = get_db()["alpha_registry"]
    hof = get_db()[COLLECTION]
    promoted = 0
    async for doc in reg.find({}):
        if not is_promotable(doc):
            continue
        await hof.update_one(
            {"expression": doc.get("expression")},
            {"$set": {
                "name": doc.get("name"),
                "expression": doc.get("expression"),
                "source": doc.get("source"),
                "metrics": doc.get("metrics"),
                "fold_sharpes": doc.get("fold_sharpes"),
                "promoted_at": datetime.now(timezone.utc),
                "status": "active",
            }},
            upsert=True,
        )
        promoted += 1
    active = await hof.count_documents({"status": "active"})
    logger.info(f"Hall of Fame refresh: promoted={promoted} active={active}")
    return {"promoted": promoted, "active": active}


async def list_hall_of_fame(limit: int = 50) -> list:
    cursor = (get_db()[COLLECTION]
              .find({"status": "active"}, {"_id": 0})
              .sort("promoted_at", -1).limit(limit))
    return await cursor.to_list(length=limit)


async def demote(expression: str, reason: str) -> dict:
    res = await get_db()[COLLECTION].update_one(
        {"expression": expression, "status": "active"},
        {"$set": {"status": "demoted",
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
