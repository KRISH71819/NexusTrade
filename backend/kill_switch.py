"""
Global Kill Switch — emergency stop for all new buy orders.

When the kill switch is ON:
  - NO new BUY orders are placed in EITHER paper or live mode
  - SELL orders continue to execute normally (to exit positions / protect capital)
  - The switch state is persisted in MongoDB and survives server restarts

The kill switch can be toggled via:
  - API endpoint: POST /api/trading/kill-switch
  - Config/env: KILL_SWITCH_ENABLED=true
  - Direct DB update (for emergencies)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from database import get_db

logger = logging.getLogger(__name__)

SYSTEM_STATE_COLLECTION = "system_state"
KILL_SWITCH_KEY = "kill_switch"


async def _get_collection():
    """Get the system_state collection."""
    db = get_db()
    return db[SYSTEM_STATE_COLLECTION]


async def is_kill_switch_on() -> bool:
    """
    Check if the kill switch is active.
    Returns True if buys should be blocked.
    """
    try:
        collection = await _get_collection()
        doc = await collection.find_one({"_id": KILL_SWITCH_KEY})

        if doc is None:
            # Not yet initialized — check config default
            from config import settings
            return settings.kill_switch_enabled

        return doc.get("enabled", False)

    except Exception as e:
        logger.error(f"Error checking kill switch: {e}")
        # On error, err on the side of caution — block buys
        return True


async def set_kill_switch(enabled: bool, source: str = "api") -> dict:
    """
    Toggle the kill switch ON or OFF.

    Args:
        enabled: True to block all buys, False to resume normal trading
        source: Who toggled it ("api", "scheduler", "system")

    Returns:
        dict with the new state
    """
    try:
        collection = await _get_collection()
        now = datetime.now(timezone.utc)

        result = await collection.update_one(
            {"_id": KILL_SWITCH_KEY},
            {
                "$set": {
                    "enabled": enabled,
                    "toggled_at": now,
                    "toggled_by": source,
                },
                "$push": {
                    "history": {
                        "$each": [{
                            "enabled": enabled,
                            "at": now,
                            "by": source,
                        }],
                        "$slice": -50,  # Keep last 50 toggle events
                    }
                },
            },
            upsert=True,
        )

        action = "ACTIVATED" if enabled else "DEACTIVATED"
        logger.warning(f"🚨 KILL SWITCH {action} by {source}")

        return {
            "enabled": enabled,
            "toggled_at": now.isoformat(),
            "toggled_by": source,
        }

    except Exception as e:
        logger.error(f"Failed to toggle kill switch: {e}")
        return {"error": str(e)}


async def get_kill_switch_status() -> dict:
    """Get full kill switch status including history."""
    try:
        collection = await _get_collection()
        doc = await collection.find_one({"_id": KILL_SWITCH_KEY})

        if doc is None:
            from config import settings
            return {
                "enabled": settings.kill_switch_enabled,
                "toggled_at": None,
                "toggled_by": "config_default",
                "history": [],
            }

        return {
            "enabled": doc.get("enabled", False),
            "toggled_at": doc.get("toggled_at", "").isoformat() if doc.get("toggled_at") else None,
            "toggled_by": doc.get("toggled_by", "unknown"),
            "history": doc.get("history", [])[-10:],  # Last 10 events
        }

    except Exception as e:
        logger.error(f"Error getting kill switch status: {e}")
        return {"enabled": True, "error": str(e)}  # Err on safe side


async def initialize_kill_switch():
    """Seed the kill switch document if it doesn't exist."""
    try:
        collection = await _get_collection()
        existing = await collection.find_one({"_id": KILL_SWITCH_KEY})

        if existing is None:
            from config import settings
            await collection.insert_one({
                "_id": KILL_SWITCH_KEY,
                "enabled": settings.kill_switch_enabled,
                "toggled_at": datetime.now(timezone.utc),
                "toggled_by": "system_init",
                "history": [],
            })
            logger.info(f"Kill switch initialized (enabled={settings.kill_switch_enabled})")

    except Exception as e:
        logger.error(f"Failed to initialize kill switch: {e}")
