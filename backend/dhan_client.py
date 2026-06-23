"""
Dhan Broker API Client — handles authentication, order execution, and portfolio queries.

Key features:
  - TOTP-based auto-token-renewal (no manual daily token refresh)
  - Rate limiting (max 10 orders/sec)
  - Exponential backoff on DH-904 rate limit errors
  - Comprehensive error mapping for all Dhan error codes
  - Connection validation on startup
  - All methods are safe to call — errors return dicts, never raise
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import pyotp

from config import settings

logger = logging.getLogger(__name__)

# ── Dhan Error Code Mapping ──────────────────────────────────────────────────
DHAN_ERRORS = {
    "DH-901": "Invalid authentication — access token expired or invalid",
    "DH-904": "Rate limit exceeded — too many requests",
    "DH-905": "Invalid input — missing required fields or bad values",
    "DH-906": "Order rejected — cannot process this order",
    "DH-908": "Dhan internal server error — try again later",
}


class DhanClient:
    """Singleton wrapper around the dhanhq SDK with auto-TOTP auth."""

    def __init__(self):
        self._dhan = None
        self._last_auth_time: Optional[datetime] = None
        self._auth_lock = asyncio.Lock()
        self._order_semaphore = asyncio.Semaphore(10)  # max 10 concurrent orders
        self._last_order_time = 0.0
        self._min_order_interval = 0.12  # ~8 orders/sec max (safe under 10/sec limit)
        self._consecutive_failures = 0
        self._initialized = False
        # Runtime credentials (injected from UI / MongoDB, override .env)
        self._runtime_client_id: Optional[str] = None
        self._runtime_pin: Optional[str] = None
        self._runtime_totp_secret: Optional[str] = None
        self._runtime_access_token: Optional[str] = None

    def configure(
        self,
        client_id: str,
        pin: str = "",
        totp_secret: str = "",
        access_token: str = "",
    ):
        """Inject Dhan credentials at runtime (from UI or MongoDB)."""
        self._runtime_client_id = client_id
        self._runtime_pin = pin
        self._runtime_totp_secret = totp_secret
        self._runtime_access_token = access_token
        # Reset auth state so next call triggers fresh authentication
        self._dhan = None
        self._initialized = False
        self._last_auth_time = None
        self._consecutive_failures = 0
        logger.info(f"Dhan client configured at runtime for client {client_id}")

    def reset(self):
        """Clear runtime credentials (when switching back to paper)."""
        self._runtime_client_id = None
        self._runtime_pin = None
        self._runtime_totp_secret = None
        self._runtime_access_token = None
        self._dhan = None
        self._initialized = False
        self._last_auth_time = None
        logger.info("Dhan client reset — runtime credentials cleared")

    @property
    def _effective_client_id(self) -> str:
        return self._runtime_client_id or settings.dhan_client_id

    @property
    def _effective_pin(self) -> str:
        return self._runtime_pin or settings.dhan_pin

    @property
    def _effective_totp_secret(self) -> str:
        return self._runtime_totp_secret or settings.dhan_totp_secret

    @property
    def _effective_access_token(self) -> str:
        return (
            self._runtime_access_token
            or settings.dhan_access_token
            or ""
        )

    @property
    def is_configured(self) -> bool:
        """Check if Dhan credentials are available (runtime or .env)."""
        has_client_id = bool(self._effective_client_id)
        has_auth = bool(self._effective_totp_secret) or bool(self._effective_access_token)
        return has_client_id and has_auth

    async def initialize(self) -> dict:
        """Initialize the Dhan client. Call once on startup."""
        if not self.is_configured:
            logger.info("Dhan API not configured — live trading disabled")
            return {"status": "not_configured"}

        try:
            await self._authenticate()
            self._initialized = True
            logger.info("Dhan API client initialized successfully")
            return {"status": "connected"}
        except Exception as e:
            logger.error(f"Dhan initialization failed: {e}")
            return {"status": "error", "error": str(e)}

    async def _authenticate(self):
        """Generate a new access token using TOTP, or use a pre-provided access token."""
        async with self._auth_lock:
            try:
                from dhanhq import dhanhq as DhanHQ
                from dhanhq import DhanContext

                client_id = self._effective_client_id
                totp_secret = self._effective_totp_secret
                pin = self._effective_pin
                direct_token = self._effective_access_token

                access_token = ""

                # Strategy 1: Try TOTP auto-login if we have a TOTP secret
                if totp_secret:
                    try:
                        totp = pyotp.TOTP(totp_secret)
                        otp_code = totp.now()
                        logger.info(
                            f"Generating Dhan access token via TOTP "
                            f"for client {client_id}"
                        )

                        from dhanhq import DhanLogin
                        login = DhanLogin(client_id)
                        token_response = login.generate_token(
                            pin=pin, totp=otp_code
                        )
                        access_token = (
                            token_response.get("accessToken")
                            or token_response.get("data", {}).get(
                                "accessToken", ""
                            )
                            or token_response.get("token", "")
                        )

                        if access_token:
                            logger.info(
                                "Dhan access token generated via TOTP"
                            )
                        else:
                            logger.warning(
                                f"TOTP response had no token: "
                                f"{token_response}"
                            )
                    except Exception as totp_err:
                        logger.warning(
                            f"TOTP auto-login failed: {totp_err}"
                        )

                # Strategy 2: Use pre-provided access token (from UI)
                if not access_token and direct_token:
                    access_token = direct_token
                    logger.info(
                        "Using pre-provided Dhan access token"
                    )

                # Strategy 3: Check environment variable as last resort
                if not access_token:
                    import os
                    access_token = os.environ.get(
                        "DHAN_ACCESS_TOKEN", ""
                    )
                    if access_token:
                        logger.info(
                            "Using DHAN_ACCESS_TOKEN from environment"
                        )

                if not access_token:
                    raise ValueError(
                        "All authentication methods failed. "
                        "Please provide either a TOTP secret or "
                        "a Dhan Access Token."
                    )

                # Initialize the main client (v2.2.0 API)
                context = DhanContext(client_id, access_token)
                self._dhan = DhanHQ(context)
                self._last_auth_time = datetime.now(timezone.utc)
                self._consecutive_failures = 0

            except Exception as e:
                logger.error(f"Dhan authentication failed: {e}")
                self._dhan = None
                raise

    async def _ensure_authenticated(self):
        """Re-authenticate if token is stale (>20 hours old)."""
        if self._dhan is None:
            await self._authenticate()
            return

        if self._last_auth_time:
            age_hours = (datetime.now(timezone.utc) - self._last_auth_time).total_seconds() / 3600
            if age_hours > 20:  # Re-auth before 24h expiry
                logger.info(f"Dhan token is {age_hours:.1f}h old — refreshing")
                await self._authenticate()

    async def _rate_limited_call(self, func, *args, **kwargs):
        """Execute a Dhan API call with rate limiting and error handling."""
        if not self._initialized and not self.is_configured:
            return {"status": "error", "error": "Dhan not configured"}

        await self._ensure_authenticated()
        if self._dhan is None:
            return {"status": "error", "error": "Dhan not authenticated"}

        async with self._order_semaphore:
            # Enforce minimum interval between orders
            now = time.monotonic()
            elapsed = now - self._last_order_time
            if elapsed < self._min_order_interval:
                await asyncio.sleep(self._min_order_interval - elapsed)

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Run sync dhanhq call in thread pool
                    result = await asyncio.wait_for(
                        asyncio.to_thread(func, *args, **kwargs),
                        timeout=30.0,
                    )
                    self._last_order_time = time.monotonic()
                    self._consecutive_failures = 0

                    # Check for Dhan-specific error codes in response
                    if isinstance(result, dict):
                        error_code = result.get("errorCode", "") or result.get("status", "")
                        if error_code in DHAN_ERRORS:
                            error_msg = DHAN_ERRORS[error_code]
                            logger.warning(f"Dhan API error {error_code}: {error_msg}")

                            if error_code == "DH-904":
                                # Rate limited — exponential backoff
                                wait = min(2 ** attempt, 16)
                                logger.info(f"Rate limited — waiting {wait}s before retry")
                                await asyncio.sleep(wait)
                                continue

                            if error_code == "DH-901":
                                # Auth expired — re-authenticate and retry
                                logger.info("Token expired — re-authenticating")
                                await self._authenticate()
                                continue

                            return {"status": "error", "error": error_msg, "dhan_code": error_code, "raw": result}

                    return result

                except asyncio.TimeoutError:
                    logger.warning(f"Dhan API timeout (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        self._consecutive_failures += 1
                        return {"status": "error", "error": "Dhan API timeout after 30s"}

                except Exception as e:
                    logger.error(f"Dhan API error (attempt {attempt + 1}): {e}")
                    self._consecutive_failures += 1
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        return {"status": "error", "error": str(e)}

    # ═══════════════════════════════════════════════════════════════════════════
    #   ACCOUNT QUERIES
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_fund_limits(self) -> dict:
        """Get account balance and margin details."""
        return await self._rate_limited_call(self._dhan.get_fund_limits)

    async def get_holdings(self) -> dict:
        """Get current demat holdings (delivery positions)."""
        return await self._rate_limited_call(self._dhan.get_holdings)

    async def get_positions(self) -> dict:
        """Get current open positions (intraday + delivery)."""
        return await self._rate_limited_call(self._dhan.get_positions)

    async def get_order_list(self) -> dict:
        """Get today's order list."""
        return await self._rate_limited_call(self._dhan.get_order_list)

    async def test_connection(self) -> dict:
        """Test Dhan API connectivity by fetching fund limits."""
        try:
            result = await self.get_fund_limits()
            if isinstance(result, dict) and result.get("status") == "error":
                return {"connected": False, "error": result.get("error", "Unknown error")}

            # Extract useful info from fund limits response
            data = result.get("data", result)
            return {
                "connected": True,
                "client_id": self._effective_client_id,
                "available_balance": data.get("availabelBalance", data.get("availableBalance", 0)),
                "utilized_amount": data.get("utilizedAmount", 0),
                "collateral": data.get("collateralAmount", 0),
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════════════════
    #   ORDER PLACEMENT
    # ═══════════════════════════════════════════════════════════════════════════

    async def place_buy_order(
        self,
        security_id: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float = 0.0,
    ) -> dict:
        """
        Place a BUY order on Dhan.

        Args:
            security_id: Dhan security ID for the instrument
            quantity: Number of shares to buy
            order_type: "MARKET" or "LIMIT"
            price: Required for LIMIT orders, ignored for MARKET
        """
        logger.info(
            f"[DHAN BUY] security_id={security_id}, qty={quantity}, "
            f"type={order_type}, price={price}"
        )

        kwargs = {
            "security_id": security_id,
            "exchange_segment": "NSE_EQ",
            "transaction_type": "BUY",
            "quantity": quantity,
            "order_type": order_type,
            "product_type": "CNC",  # Delivery (Cash & Carry) for swing trading
            "validity": "DAY",
        }
        if order_type == "LIMIT" and price > 0:
            kwargs["price"] = price

        result = await self._rate_limited_call(self._dhan.place_order, **kwargs)

        if isinstance(result, dict):
            order_id = result.get("orderId") or result.get("data", {}).get("orderId")
            if order_id:
                logger.info(f"[DHAN BUY SUCCESS] Order ID: {order_id}")
                return {"status": "success", "order_id": order_id, "raw": result}

            error = result.get("error") or result.get("remarks") or str(result)
            logger.warning(f"[DHAN BUY FAILED] {error}")
            return {"status": "error", "error": error, "raw": result}

        return {"status": "error", "error": f"Unexpected response: {result}"}

    async def place_sell_order(
        self,
        security_id: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float = 0.0,
    ) -> dict:
        """
        Place a SELL order on Dhan.

        Args:
            security_id: Dhan security ID for the instrument
            quantity: Number of shares to sell
            order_type: "MARKET" or "LIMIT"
            price: Required for LIMIT orders, ignored for MARKET
        """
        logger.info(
            f"[DHAN SELL] security_id={security_id}, qty={quantity}, "
            f"type={order_type}, price={price}"
        )

        kwargs = {
            "security_id": security_id,
            "exchange_segment": "NSE_EQ",
            "transaction_type": "SELL",
            "quantity": quantity,
            "order_type": order_type,
            "product_type": "CNC",
            "validity": "DAY",
        }
        if order_type == "LIMIT" and price > 0:
            kwargs["price"] = price

        result = await self._rate_limited_call(self._dhan.place_order, **kwargs)

        if isinstance(result, dict):
            order_id = result.get("orderId") or result.get("data", {}).get("orderId")
            if order_id:
                logger.info(f"[DHAN SELL SUCCESS] Order ID: {order_id}")
                return {"status": "success", "order_id": order_id, "raw": result}

            error = result.get("error") or result.get("remarks") or str(result)
            logger.warning(f"[DHAN SELL FAILED] {error}")
            return {"status": "error", "error": error, "raw": result}

        return {"status": "error", "error": f"Unexpected response: {result}"}

    def get_status(self) -> dict:
        """Get client status for diagnostics."""
        return {
            "configured": self.is_configured,
            "initialized": self._initialized,
            "authenticated": self._dhan is not None,
            "last_auth": self._last_auth_time.isoformat() if self._last_auth_time else None,
            "consecutive_failures": self._consecutive_failures,
        }


# ── Module-level singleton ───────────────────────────────────────────────────
dhan_client = DhanClient()
