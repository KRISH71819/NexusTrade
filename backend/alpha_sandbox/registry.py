"""
Alpha registry — Mongo store of tested candidate alphas (Phase 1).
Later phases (Hall of Fame) build on this collection.
"""
import logging
from datetime import datetime, timezone

from database import get_db

logger = logging.getLogger(__name__)

COLLECTION = "alpha_registry"


def _coll():
    return get_db()[COLLECTION]


async def save_alpha_result(expression, name, info, metrics, fold_sharpes, gates, source: str = "classic") -> dict:
    doc = {
        "name": name,
        "expression": expression,
        "source": source,   # "classic" | "llm"
        "created_at": datetime.now(timezone.utc),
        "info": info,
        "metrics": metrics,
        "fold_sharpes": fold_sharpes,
        "gates": gates,
        "status": "approved" if gates.get("all") else "rejected",
    }
    await _coll().insert_one(doc)
    logger.info(f"alpha_registry: saved '{name}' status={doc['status']}")
    return doc


async def list_alphas(limit: int = 20, status: str | None = None) -> list:
    query = {"status": status} if status else {}
    cursor = _coll().find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)
