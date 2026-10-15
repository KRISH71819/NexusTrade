"""
Size Guard — monitors MongoDB Atlas free tier (512MB) and prunes stale logs.
Run periodically via scheduler or manually via API.
"""
import logging
from database import get_db

logger = logging.getLogger(__name__)

ATLAS_FREE_TIER_BYTES = 512 * 1024 * 1024  # 512 MB
PRUNE_THRESHOLD_PCT = 0.85                 # Trigger prune at 85% capacity
MAX_LOG_RETENTION = 15000                  # Keep last 15k docs per collection

async def check_and_enforce_storage_budget() -> dict:
    """Checks DB size and prunes old logs if threshold is breached."""
    try:
        db = get_db()
        stats = await db.command({"dbStats": 1, "scale": 1})
        used_bytes = stats.get("dataSize", 0) + stats.get("indexSize", 0)
        usage_pct = used_bytes / ATLAS_FREE_TIER_BYTES
        
        logger.info(f"MongoDB storage: {used_bytes/1024/1024:.1f}MB / 512MB ({usage_pct:.1%})")
        
        if usage_pct >= PRUNE_THRESHOLD_PCT:
            logger.warning("Storage budget exceeded. Pruning old logs...")
            pruned = await _prune_old_logs(db)
            return {"status": "pruned", "usage_pct": usage_pct, "pruned_counts": pruned}
            
        return {"status": "ok", "usage_pct": usage_pct, "pruned_counts": {}}
    except Exception as e:
        logger.warning(f"Size guard failed: {e}")
        return {"status": "error", "error": str(e)}

async def _prune_old_logs(db) -> dict:
    """Keeps only the most recent MAX_LOG_RETENTION documents per log collection."""
    pruned = {}
    for coll_name in ["analysis_log", "trades", "cycle_stats", "score_history"]:
        coll = db[coll_name]
        count = await coll.count_documents({})
        if count > MAX_LOG_RETENTION:
            # Find the cutoff timestamp
            cutoff_doc = await coll.find({}, {"timestamp": 1}) \
                                   .sort("timestamp", -1) \
                                   .skip(MAX_LOG_RETENTION) \
                                   .limit(1) \
                                   .to_list(1)
            if cutoff_doc:
                cutoff_time = cutoff_doc[0]["timestamp"]
                res = await coll.delete_many({"timestamp": {"$lt": cutoff_time}})
                pruned[coll_name] = res.deleted_count
                logger.info(f"Pruned {res.deleted_count} old docs from {coll_name}")
    return pruned
