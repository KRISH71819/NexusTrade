"""IST time helpers (ported verbatim from legacy market_time.py).
HF containers run UTC; every trading-day rollover must use IST."""
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

def ist_now() -> datetime:
    return datetime.now(IST)

def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)

def ist_today_str() -> str:
    return ist_now().strftime("%Y-%m-%d")

def ist_day_start_utc(now: datetime | None = None) -> datetime:
    now_ist = to_ist(now) if now is not None else ist_now()
    midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_ist.astimezone(timezone.utc)

def is_stale_day_open(stored_date_str: str | None, current_date_str: str | None = None) -> bool:
    if not stored_date_str:
        return True
    today = current_date_str or ist_today_str()
    return stored_date_str != today
