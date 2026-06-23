"""
Trading Mode API — mode switching, kill switch, Dhan credentials, and account management.

SaaS-ready: Dhan credentials can be entered from the UI and are persisted in MongoDB.
No need to edit .env to switch between paper and live trading.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
import logging

from config import settings
from database import get_db
from kill_switch import (
    is_kill_switch_on,
    set_kill_switch,
    get_kill_switch_status,
)

router = APIRouter()
logger = logging.getLogger(__name__)

SYSTEM_STATE_COLLECTION = "system_state"
DHAN_CREDS_KEY = "dhan_credentials"


class ModeSwitch(BaseModel):
    mode: str = Field(..., pattern="^(paper|live)$")


class KillSwitchToggle(BaseModel):
    enabled: bool


class LiveCapitalSetting(BaseModel):
    max_capital: float = Field(
        ge=0, description="Maximum capital the bot can use from Dhan account"
    )


class DhanCredentials(BaseModel):
    client_id: str = Field(..., min_length=1)
    pin: str = Field(default="")
    totp_secret: str = Field(default="")
    access_token: str = Field(default="")


# ═══════════════════════════════════════════════════════════════════════════════
#   DHAN CREDENTIALS (persist in MongoDB — SaaS-ready)
# ═══════════════════════════════════════════════════════════════════════════════

async def _load_saved_credentials() -> Optional[dict]:
    """Load Dhan credentials from MongoDB (if previously saved via UI)."""
    try:
        db = get_db()
        doc = await db[SYSTEM_STATE_COLLECTION].find_one(
            {"_id": DHAN_CREDS_KEY}
        )
        return doc
    except Exception as e:
        logger.error(f"Error loading saved Dhan credentials: {e}")
        return None


async def load_and_configure_dhan():
    """
    Load saved credentials from MongoDB and configure the Dhan client.
    Called on startup and before mode switches.
    """
    doc = await _load_saved_credentials()
    if doc and doc.get("client_id"):
        from dhan_client import dhan_client
        dhan_client.configure(
            client_id=doc["client_id"],
            pin=doc.get("pin", ""),
            totp_secret=doc.get("totp_secret", ""),
            access_token=doc.get("access_token", ""),
        )
        return True
    return False


@router.post("/trading/dhan/credentials")
async def save_dhan_credentials(payload: DhanCredentials):
    """Save Dhan credentials from the UI into MongoDB."""
    if not payload.totp_secret and not payload.access_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide at least one of: totp_secret (for auto-login) "
                "or access_token (manual token from api.dhan.co)"
            ),
        )

    try:
        db = get_db()
        now = datetime.now(timezone.utc)

        await db[SYSTEM_STATE_COLLECTION].update_one(
            {"_id": DHAN_CREDS_KEY},
            {
                "$set": {
                    "client_id": payload.client_id,
                    "pin": payload.pin,
                    "totp_secret": payload.totp_secret,
                    "access_token": payload.access_token,
                    "updated_at": now,
                }
            },
            upsert=True,
        )

        # Configure the live singleton immediately
        from dhan_client import dhan_client
        dhan_client.configure(
            client_id=payload.client_id,
            pin=payload.pin,
            totp_secret=payload.totp_secret,
            access_token=payload.access_token,
        )

        logger.info(f"Dhan credentials saved for client {payload.client_id}")

        return {
            "message": "Dhan credentials saved successfully",
            "configured": True,
            "client_id": _mask(payload.client_id),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to save credentials: {e}"
        )


@router.get("/trading/dhan/credentials")
async def get_dhan_credentials():
    """Check if Dhan credentials are saved (never returns secrets)."""
    doc = await _load_saved_credentials()

    from dhan_client import dhan_client

    if doc and doc.get("client_id"):
        return {
            "configured": True,
            "client_id": _mask(doc["client_id"]),
            "has_totp": bool(doc.get("totp_secret")),
            "has_access_token": bool(doc.get("access_token")),
            "updated_at": (
                doc["updated_at"].isoformat()
                if doc.get("updated_at")
                else None
            ),
            "dhan_initialized": dhan_client._initialized,
        }

    # Check if .env has credentials as fallback
    env_configured = bool(
        settings.dhan_client_id and settings.dhan_totp_secret
    )
    return {
        "configured": env_configured,
        "client_id": _mask(settings.dhan_client_id) if env_configured else None,
        "has_totp": bool(settings.dhan_totp_secret),
        "has_access_token": False,
        "source": "env" if env_configured else None,
        "dhan_initialized": dhan_client._initialized,
    }


@router.delete("/trading/dhan/credentials")
async def delete_dhan_credentials():
    """Clear saved Dhan credentials and reset the client."""
    try:
        db = get_db()
        await db[SYSTEM_STATE_COLLECTION].delete_one(
            {"_id": DHAN_CREDS_KEY}
        )

        from dhan_client import dhan_client
        dhan_client.reset()

        # If currently in live mode, force back to paper
        if settings.trading_mode == "live":
            settings.trading_mode = "paper"
            logger.warning(
                "Forced back to PAPER mode after credential deletion"
            )

        return {
            "message": "Dhan credentials deleted. Switched to paper mode.",
            "mode": settings.trading_mode,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete credentials: {e}",
        )


def _mask(value: str) -> str:
    """Mask a credential string for safe display (e.g. '111***165')."""
    if not value or len(value) < 4:
        return "***"
    return value[:3] + "***" + value[-3:]


# ═══════════════════════════════════════════════════════════════════════════════
#   TRADING MODE
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/trading/mode")
async def get_trading_mode():
    """Get current trading mode, kill switch status, and Dhan connection info."""
    kill_status = await get_kill_switch_status()

    from dhan_client import dhan_client
    dhan_configured = dhan_client.is_configured

    # Also check MongoDB for saved credentials
    if not dhan_configured:
        doc = await _load_saved_credentials()
        if doc and doc.get("client_id"):
            dhan_configured = True

    return {
        "mode": settings.trading_mode,
        "kill_switch": kill_status,
        "dhan_configured": dhan_configured,
        "live_capital_cap": getattr(settings, "live_capital_cap", 100000.0),
    }


@router.post("/trading/capital-cap")
async def set_capital_cap(payload: LiveCapitalSetting):
    """Set the maximum capital allowed for live trading."""
    settings.live_capital_cap = payload.max_capital
    cap_display = (
        "Full Investment"
        if payload.max_capital <= 0
        else f"Rs.{payload.max_capital}"
    )
    return {
        "message": f"Capital cap updated to {cap_display}",
        "cap": settings.live_capital_cap,
    }


@router.post("/trading/mode")
async def switch_trading_mode(payload: ModeSwitch):
    """Switch between paper and live trading mode."""
    if payload.mode == "live":
        from dhan_client import dhan_client

        # If not already configured, try loading from MongoDB
        if not dhan_client.is_configured:
            loaded = await load_and_configure_dhan()
            if not loaded:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Dhan credentials not found. Please enter your "
                        "Dhan Client ID and TOTP Secret to enable "
                        "live trading."
                    ),
                )

        # Attempt to connect/authenticate
        if not dhan_client._initialized:
            init_result = await dhan_client.initialize()
            if init_result.get("status") != "connected":
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Cannot connect to Dhan: "
                        f"{init_result.get('error', 'Unknown error')}. "
                        f"If TOTP login failed, try providing an "
                        f"Access Token from api.dhan.co instead."
                    ),
                )

    # Update the runtime setting
    settings.trading_mode = payload.mode

    return {
        "mode": settings.trading_mode,
        "message": f"Switched to {payload.mode.upper()} trading mode",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   KILL SWITCH
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/trading/kill-switch")
async def toggle_kill_switch(payload: KillSwitchToggle):
    """Toggle the global kill switch ON/OFF."""
    result = await set_kill_switch(payload.enabled, source="api")

    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])

    action = "ACTIVATED" if payload.enabled else "DEACTIVATED"
    return {
        **result,
        "message": (
            f"Kill switch {action}. "
            + (
                "All new buys are blocked."
                if payload.enabled
                else "Normal trading resumed."
            )
        ),
    }


@router.get("/trading/kill-switch")
async def get_kill_switch():
    """Get kill switch status."""
    return await get_kill_switch_status()


# ═══════════════════════════════════════════════════════════════════════════════
#   DHAN ACCOUNT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/trading/dhan/status")
async def get_dhan_status():
    """Get Dhan API connection status and account overview."""
    from dhan_client import dhan_client

    if not dhan_client.is_configured:
        return {
            "configured": False,
            "connected": False,
            "message": (
                "Dhan not configured. Enter your credentials "
                "from the Trading Controls panel."
            ),
        }

    status = dhan_client.get_status()

    if not dhan_client._initialized:
        init_result = await dhan_client.initialize()
        status["initialization"] = init_result

    # Try to get connection status
    connection = await dhan_client.test_connection()
    return {
        **status,
        **connection,
    }


@router.get("/trading/dhan/funds")
async def get_dhan_funds():
    """Get real-time Dhan account balance and margin details."""
    from dhan_client import dhan_client

    if not dhan_client.is_configured:
        raise HTTPException(status_code=400, detail="Dhan not configured")

    result = await dhan_client.get_fund_limits()
    if isinstance(result, dict) and result.get("status") == "error":
        raise HTTPException(
            status_code=503,
            detail=result.get("error", "Dhan API error"),
        )

    return result


@router.get("/trading/dhan/holdings")
async def get_dhan_holdings():
    """Get current Dhan demat holdings."""
    from dhan_client import dhan_client

    if not dhan_client.is_configured:
        raise HTTPException(status_code=400, detail="Dhan not configured")

    result = await dhan_client.get_holdings()
    if isinstance(result, dict) and result.get("status") == "error":
        raise HTTPException(
            status_code=503,
            detail=result.get("error", "Dhan API error"),
        )

    return result


@router.post("/trading/dhan/sync")
async def sync_dhan_portfolio():
    """Force-sync the live portfolio with Dhan account data."""
    from ledger import sync_live_portfolio

    result = await sync_live_portfolio()
    if result.get("error"):
        raise HTTPException(status_code=503, detail=result["error"])

    return {
        "message": "Live portfolio synced with Dhan account",
        "portfolio": result,
    }
