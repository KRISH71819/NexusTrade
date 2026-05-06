"""
MongoDB connection + collection initialization.
Seeds the portfolio with starting balance on first run.
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


# ── Collections ──────────────────────────────────────────────────────────────

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


# ── Indexes ──────────────────────────────────────────────────────────────────

async def _ensure_indexes():
    """Create indexes for efficient queries."""
    db = get_db()

    # Trades: query by ticker and timestamp
    await db["trades"].create_index([("ticker", 1), ("timestamp", -1)])
    await db["trades"].create_index([("timestamp", -1)])

    # Analysis log: query by ticker and timestamp
    await db["analysis_log"].create_index([("ticker", 1), ("timestamp", -1)])
    await db["analysis_log"].create_index([("timestamp", -1)])

    # Market data: one doc per ticker, indexed
    await db["market_data"].create_index("ticker", unique=True)

    # Portfolio history: time-series
    await db["portfolio_history"].create_index([("timestamp", -1)])

    logger.info("MongoDB indexes ensured.")


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
