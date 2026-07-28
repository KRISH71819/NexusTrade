"""
IST time helpers.

All portfolio/trade writes elsewhere use timezone.utc, but the scheduler cron
and every trading-day rollover (daily circuit breaker, daily report) must be
computed in India Standard Time. Hugging Face containers run in UTC, so we must
never derive the trading day from the container clock.

India observes no DST, so a fixed UTC+5:30 offset is exact and needs no tzdata.
"""

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def ist_now() -> datetime:
    """Current time as an aware datetime in IST."""
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    """Convert any datetime to IST. Naive datetimes are assumed to be UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist_today_str() -> str:
    """Today's IST calendar date as an ISO string (YYYY-MM-DD)."""
    return ist_now().strftime("%Y-%m-%d")


def ist_day_start_utc(now: datetime | None = None) -> datetime:
    """
    UTC instant corresponding to 00:00 IST of the current IST day.

    Useful for querying UTC-stamped trade/analysis documents that belong to
    'today' in trading terms.
    """
    now_ist = to_ist(now) if now is not None else ist_now()
    midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_ist.astimezone(timezone.utc)


def is_stale_day_open(stored_date_str: str | None, current_date_str: str | None = None) -> bool:
    """
    Return True if a stored day-open IST date is missing or belongs to a
    previous IST day and therefore must be re-stamped.
    """
    if not stored_date_str:
        return True
    today = current_date_str or ist_today_str()
    return stored_date_str != today
