"""
NexusTrade V1 -> V2 MongoDB Cutover & Migration Tool
Standing Rule #8: NEVER drop or rename 'ohlcv_history'.

Archives legacy V1 collections by renaming them to 'legacy_v1_<name>'.
Initializes V2 collections ('eod_book', 'eod_snapshots', 'alpha_hof', 'research_runs', 'audit_log')
with proper indexing. Supports --dry-run for non-mutating preview.
"""

import argparse
import asyncio
import logging
import os
import sys
from typing import Dict, Any, List

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v2_migration")

# Standing Rule #8: Must never touch or rename these collections
SACROSANCT_COLLECTIONS = frozenset({"ohlcv_history"})

# Known legacy V1 collections to be archived
LEGACY_V1_COLLECTIONS = (
    "trades",
    "portfolio",
    "analyses",
    "analysis",
    "market_data",
    "watchlist",
    "snapshots",
    "gemini_cache",
    "paper_orders",
)

# V2 target collections and required index specifications
V2_INDEX_SPECS: Dict[str, List[Any]] = {
    "eod_book": [
        {"keys": [("book_id", 1)], "unique": True},
    ],
    "eod_snapshots": [
        {"keys": [("date", -1)]},
    ],
    "alpha_hof": [
        {"keys": [("alpha_id", 1)], "unique": True},
        {"keys": [("status", 1)]},
    ],
    "research_runs": [
        {"keys": [("run_id", 1)], "unique": True},
        {"keys": [("created_at", -1)]},
    ],
    "audit_log": [
        {"keys": [("timestamp", -1)]},
        {"keys": [("event_type", 1)]},
    ],
}


async def run_migration(db: Any, dry_run: bool = True) -> Dict[str, Any]:
    """
    Execute V1 -> V2 MongoDB collection cutover.
    
    Args:
        db: Async database client/collection handle (e.g. AsyncIOMotorDatabase or PyMongo AsyncDatabase)
        dry_run: When True, simulates changes without executing any renames or index builds.
    
    Returns:
        Structured migration summary dictionary.
    """
    logger.info("Starting V1 -> V2 Migration (dry_run=%s)", dry_run)
    
    # Get existing collection names
    existing_cols = await db.list_collection_names()
    existing_set = set(existing_cols)
    
    summary: Dict[str, Any] = {
        "dry_run": dry_run,
        "sacrosanct_preserved": [],
        "renamed": [],
        "skipped_existing_archive": [],
        "v2_collections_initialized": [],
    }
    
    # 1. Enforce Sacrosanct Collections Check
    for col in SACROSANCT_COLLECTIONS:
        if col in existing_set:
            logger.info("STANDING RULE #8 VERIFIED: '%s' preserved intact.", col)
            summary["sacrosanct_preserved"].append(col)

    # 2. Archive V1 Collections
    for legacy_name in LEGACY_V1_COLLECTIONS:
        if legacy_name not in existing_set:
            continue
        
        # Guard against accidental corruption of sacrosanct collections
        if legacy_name in SACROSANCT_COLLECTIONS:
            logger.error("FATAL: Attempted rename of sacrosanct collection %s blocked!", legacy_name)
            continue
            
        archive_name = f"legacy_v1_{legacy_name}"
        if archive_name in existing_set:
            logger.warning("Archive target '%s' already exists. Skipping rename to prevent overwrite.", archive_name)
            summary["skipped_existing_archive"].append((legacy_name, archive_name))
            continue
            
        if dry_run:
            logger.info("[DRY RUN] Would rename collection: '%s' -> '%s'", legacy_name, archive_name)
            summary["renamed"].append({"from": legacy_name, "to": archive_name, "executed": False})
        else:
            logger.info("Executing rename: '%s' -> '%s'", legacy_name, archive_name)
            col = db[legacy_name]
            await col.rename(archive_name)
            summary["renamed"].append({"from": legacy_name, "to": archive_name, "executed": True})

    # 3. Initialize V2 Collections and Indexes
    for v2_col_name, indexes in V2_INDEX_SPECS.items():
        if dry_run:
            logger.info("[DRY RUN] Would initialize V2 collection '%s' with %d index(es)", v2_col_name, len(indexes))
            summary["v2_collections_initialized"].append({
                "collection": v2_col_name,
                "indexes": indexes,
                "executed": False,
            })
        else:
            logger.info("Initializing V2 collection '%s' with %d index(es)", v2_col_name, len(indexes))
            col = db[v2_col_name]
            for idx in indexes:
                keys = idx["keys"]
                unique = idx.get("unique", False)
                try:
                    await col.create_index(keys, unique=unique)
                except Exception as e:
                    logger.warning("Index on %s (%s, unique=%s) existing/conflict: %s", v2_col_name, keys, unique, e)
            summary["v2_collections_initialized"].append({
                "collection": v2_col_name,
                "indexes": indexes,
                "executed": True,
            })

    logger.info("V1 -> V2 Migration finished. Summary: %s", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="NexusTrade V1 -> V2 MongoDB Cutover & Migration Tool")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Simulate migration without modifying DB")
    parser.add_argument("--live", action="store_true", default=False, help="Execute live migration (mutates DB)")
    parser.add_argument("--uri", default="", help="MongoDB connection URI (defaults to MONGODB_URI env var)")
    parser.add_argument("--db", default="", help="Database name (defaults to MONGODB_DB_NAME env var)")

    args = parser.parse_args()

    # Default to dry-run unless --live is explicitly passed
    dry_run = not args.live or args.dry_run

    mongo_uri = args.uri or os.getenv("MONGODB_URI", "")
    db_name = args.db or os.getenv("MONGODB_DB_NAME", "paper_trader")

    if not mongo_uri:
        logger.error("MONGODB_URI not specified. Pass via --uri or set environment variable.")
        sys.exit(1)

    from pymongo.asynchronous.mongo_client import AsyncMongoClient
    
    async def _async_main():
        client = AsyncMongoClient(mongo_uri)
        db = client[db_name]
        try:
            summary = await run_migration(db, dry_run=dry_run)
            print("\n" + "=" * 60)
            print("MIGRATION SUMMARY REPORT")
            print("=" * 60)
            print(f"Mode: {'DRY RUN (No changes made)' if summary['dry_run'] else 'LIVE (Changes committed)'}")
            print(f"Preserved Collections: {summary['sacrosanct_preserved']}")
            print(f"Renamed Collections: {len(summary['renamed'])}")
            for r in summary['renamed']:
                print(f"  - {r['from']} -> {r['to']} (executed={r['executed']})")
            print(f"Skipped Archives: {len(summary['skipped_existing_archive'])}")
            print(f"V2 Collections Ready: {len(summary['v2_collections_initialized'])}")
            for v in summary['v2_collections_initialized']:
                print(f"  - {v['collection']} (indexes={len(v['indexes'])})")
            print("=" * 60)
        finally:
            await client.close()

    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
