"""
MongoDB connection + collection initialization.
Seeds the portfolio with starting balance on first run.
Provides separate collections for paper and live trading modes.
"""

from pymongo import AsyncMongoClient
from pymongo.errors import CollectionInvalid
from config import settings
from datetime import datetime, timezone
import logging

try:
    import certifi
except ImportError:  # pragma: no cover - optional runtime hardening
    certifi = None

logger = logging.getLogger(__name__)

# ── Global client & DB references ────────────────────────────────────────────
_client: AsyncMongoClient | None = None
_db = None


async def connect_db():
    """Initialize the async MongoDB client and return the database."""
    global _client, _db
    client_options = {
        "serverSelectionTimeoutMS": 10000,
        "connectTimeoutMS": 20000,
        "socketTimeoutMS": 20000,
    }
    if certifi is not None:
        client_options["tlsCAFile"] = certifi.where()

    _client = AsyncMongoClient(settings.mongodb_uri, **client_options)
    _db = _client[settings.mongodb_db_name]

    # Verify connection with a ping
    try:
        await _db.command("ping")
        logger.info(f"Connected to MongoDB: {settings.mongodb_db_name}")

        # Create indexes
        await _ensure_indexes()

        # Seed portfolio if it doesn't exist
        await _seed_portfolio()

    except Exception as e:
        logger.warning(
            f"MongoDB not available ({e}). "
            f"Server will start but DB operations will fail until MongoDB is running."
        )

    return _db


async def close_db():
    """Gracefully close the MongoDB connection."""
    global _client
    if _client:
        await _client.close()
        logger.info("MongoDB connection closed.")


def get_db():
    """Return the database instance."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call connect_db() first.")
    return _db


# ── Collections (Paper Trading — existing) ───────────────────────────────────

def get_portfolio_collection():
    return get_db()["portfolio"]


def get_trades_collection():
    return get_db()["trades"]


def get_analysis_collection():
    return get_db()["analysis_log"]


def get_market_data_collection():
    return get_db()["market_data"]


def get_portfolio_history_collection():
    return get_db()["portfolio_history"]


def get_score_history_collection():
    """Per-cycle score snapshots for held stocks (Batch 1.4).

    Raw data used by Batch 2.5 to set score-reduction thresholds from measured
    percentiles instead of guesses.
    """
    return get_db()["score_history"]


def get_cycle_stats_collection():
    """Per-cycle operational stats (failures, action counts) for the daily report (Batch 1.5)."""
    return get_db()["cycle_stats"]


# ── Collections (Live Trading — complete isolation) ──────────────────────────

def get_live_portfolio_collection():
    return get_db()["live_portfolio"]


def get_live_trades_collection():
    return get_db()["live_trades"]


def get_live_portfolio_history_collection():
    return get_db()["live_portfolio_history"]


# ── Collections (System State) ───────────────────────────────────────────────

def get_system_state_collection():
    return get_db()["system_state"]


# ── Mode-aware collection helpers ────────────────────────────────────────────

def get_portfolio_collection_for_mode(mode: str = "paper"):
    """Get the portfolio collection for the given trading mode."""
    if mode == "live":
        return get_live_portfolio_collection()
    return get_portfolio_collection()


def get_trades_collection_for_mode(mode: str = "paper"):
    """Get the trades collection for the given trading mode."""
    if mode == "live":
        return get_live_trades_collection()
    return get_trades_collection()


def get_portfolio_history_collection_for_mode(mode: str = "paper"):
    """Get the portfolio history collection for the given trading mode."""
    if mode == "live":
        return get_live_portfolio_history_collection()
    return get_portfolio_history_collection()


# ── Indexes ──────────────────────────────────────────────────────────────────

async def _ensure_indexes():
    """Create indexes for efficient queries."""
    db = get_db()

    # Paper trading indexes (existing)
    await db["trades"].create_index([("ticker", 1), ("timestamp", -1)])
    await db["trades"].create_index([("timestamp", -1)])
    await db["analysis_log"].create_index([("ticker", 1), ("timestamp", -1)])
    await db["analysis_log"].create_index([("timestamp", -1)])
    await db["market_data"].create_index("ticker", unique=True)
    await db["portfolio_history"].create_index([("timestamp", -1)])
    await db["score_history"].create_index([("timestamp", -1)])
    await db["cycle_stats"].create_index([("timestamp", -1)])

    # Live trading indexes (same structure, separate collections)
    await db["live_trades"].create_index([("ticker", 1), ("timestamp", -1)])
    await db["live_trades"].create_index([("timestamp", -1)])
    await db["live_portfolio_history"].create_index([("timestamp", -1)])

    logger.info("MongoDB indexes ensured (paper + live).")


# ── Seed ─────────────────────────────────────────────────────────────────────

async def _seed_portfolio():
    """Initialize portfolio with starting balance if it doesn't exist."""
    collection = get_portfolio_collection()
    existing = await collection.find_one({"_id": "main"})

    if existing is None:
        portfolio_doc = {
            "_id": "main",
            "cash": settings.initial_balance,
            "holdings": [],
            "total_value": settings.initial_balance,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "initial_balance": settings.initial_balance,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        await collection.insert_one(portfolio_doc)

        # Also record initial snapshot in history
        snapshot = {
            "timestamp": datetime.now(timezone.utc),
            "cash": settings.initial_balance,
            "holdings_value": 0.0,
            "total_value": settings.initial_balance,
        }
        await get_portfolio_history_collection().insert_one(snapshot)

        logger.info(
            f"Portfolio seeded with Rs.{settings.initial_balance:,.2f} starting balance."
        )
    else:
        logger.info(
            f"Portfolio exists — cash: Rs.{existing['cash']:,.2f}, "
            f"total: Rs.{existing['total_value']:,.2f}"
        )
