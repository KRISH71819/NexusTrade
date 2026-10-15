"""
Dhan Live Execution Safety Gate — Phase 6
Enforces multi-layered fail-safe checks before any live order can be sent to Dhan.

Gates enforced:
1. Mode Gate: Must be explicitly "live".
2. Kill Switch Gate: Global kill switch must be OFF.
3. Book Gate: EOD portfolio book status must be "OPEN" (not "HALTED").
4. Market Hours Gate: Must be regular NSE trading session (Mon-Fri 09:15 - 15:30 IST).
5. Credential Gate: Client ID, PIN, TOTP secret, and Access Token must be configured and valid.
6. Order Sanity Gate: Valid ticker, qty > 0, price > 0, side in ("BUY", "SELL").
"""

from datetime import datetime, time
from typing import NamedTuple, Optional, Dict, Any

from core.ist import to_ist, IST

NSE_MARKET_OPEN = time(9, 15)
NSE_MARKET_CLOSE = time(15, 30)


class DhanGateResult(NamedTuple):
    allowed: bool
    reason: str
    gate_failed: Optional[str] = None


def is_market_open(dt: Optional[datetime] = None) -> bool:
    """
    Check if the given datetime (or now) falls within regular NSE market trading hours:
    Monday to Friday, 09:15:00 to 15:30:00 IST.
    """
    if dt is None:
        dt_ist = datetime.now(IST)
    else:
        dt_ist = to_ist(dt)

    # 0 = Monday, ..., 4 = Friday, 5 = Saturday, 6 = Sunday
    if dt_ist.weekday() >= 5:
        return False

    current_time = dt_ist.time()
    return NSE_MARKET_OPEN <= current_time < NSE_MARKET_CLOSE


def validate_dhan_credentials(
    client_id: Optional[str],
    pin: Optional[str] = None,
    totp_secret: Optional[str] = None,
    access_token: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Validate Dhan broker credential shape.
    Credentials must be non-empty strings without whitespace-only values.
    """
    if not client_id or not client_id.strip():
        return False, "Missing or blank Dhan Client ID"
    if pin is not None and not pin.strip():
        return False, "Dhan PIN is blank"
    if totp_secret is not None and not totp_secret.strip():
        return False, "Dhan TOTP secret is blank"
    if access_token is not None and not access_token.strip():
        return False, "Dhan Access Token is blank"
    return True, "Credentials shape valid"


def can_execute_live_order(
    trading_mode: str,
    kill_switch_on: bool,
    book_status: str,
    credentials: Optional[Dict[str, str]],
    order: Dict[str, Any],
    now: Optional[datetime] = None,
) -> DhanGateResult:
    """
    Evaluate all 6 safety gates in strict order.
    Returns DhanGateResult(allowed=True, ...) only if ALL gates pass.
    """
    # Gate 1: Explicit Trading Mode
    if trading_mode != "live":
        return DhanGateResult(
            allowed=False,
            reason=f"Trading mode is '{trading_mode}', not 'live'. Live order routing blocked.",
            gate_failed="MODE_GATE",
        )

    # Gate 2: Global Kill Switch
    if kill_switch_on:
        return DhanGateResult(
            allowed=False,
            reason="Global kill switch is ACTIVE. All live orders are hard-blocked.",
            gate_failed="KILL_SWITCH_GATE",
        )

    # Gate 3: Book Status
    if book_status != "OPEN":
        return DhanGateResult(
            allowed=False,
            reason=f"EOD Book status is '{book_status}' (not 'OPEN'). Trading is halted.",
            gate_failed="BOOK_STATUS_GATE",
        )

    # Gate 4: Market Hours
    if not is_market_open(now):
        return DhanGateResult(
            allowed=False,
            reason="Current time is outside NSE trading hours (Mon-Fri 09:15-15:30 IST).",
            gate_failed="MARKET_HOURS_GATE",
        )

    # Gate 5: Credentials
    creds = credentials or {}
    valid_creds, cred_err = validate_dhan_credentials(
        client_id=creds.get("client_id"),
        pin=creds.get("pin"),
        totp_secret=creds.get("totp_secret"),
        access_token=creds.get("access_token"),
    )
    if not valid_creds:
        return DhanGateResult(
            allowed=False,
            reason=f"Invalid Dhan credentials: {cred_err}",
            gate_failed="CREDENTIAL_GATE",
        )

    # Gate 6: Order Sanity Check
    ticker = order.get("ticker", "")
    qty = order.get("quantity", order.get("qty", 0))
    price = order.get("price", 0.0)
    side = str(order.get("side", "")).upper()

    if not ticker or not isinstance(ticker, str) or not ticker.strip():
        return DhanGateResult(
            allowed=False,
            reason="Order sanity check failed: Missing or invalid ticker.",
            gate_failed="ORDER_SANITY_GATE",
        )

    try:
        qty_num = int(qty)
        if qty_num <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return DhanGateResult(
            allowed=False,
            reason=f"Order sanity check failed: Invalid quantity '{qty}' (must be positive integer).",
            gate_failed="ORDER_SANITY_GATE",
        )

    try:
        price_num = float(price)
        if price_num <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return DhanGateResult(
            allowed=False,
            reason=f"Order sanity check failed: Invalid price '{price}' (must be positive float).",
            gate_failed="ORDER_SANITY_GATE",
        )

    if side not in ("BUY", "SELL"):
        return DhanGateResult(
            allowed=False,
            reason=f"Order sanity check failed: Invalid order side '{side}' (must be 'BUY' or 'SELL').",
            gate_failed="ORDER_SANITY_GATE",
        )

    return DhanGateResult(
        allowed=True,
        reason="All live trading safety gates passed.",
        gate_failed=None,
    )
