"""
Daily one-page report and weekly summary.

Emits once per day (post-close, 15:35 IST) to:
  - Python logger (INFO level — always)
  - Telegram (if configured)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


async def _get_legacy_status_line() -> str:
    """ONE-line portfolio summary: open positions count + risk state."""
    try:
        from config import settings
        from ledger import get_portfolio_for_mode, get_daily_pnl_pct

        portfolio = await get_portfolio_for_mode(settings.trading_mode)
        n_pos = len([h for h in portfolio.get("holdings", [])
                     if h.get("quantity", 0) > 0])
        try:
            daily_pnl = await get_daily_pnl_pct(settings.trading_mode)
        except Exception:
            daily_pnl = 0.0

        risk = "OK"
        if daily_pnl <= -float(settings.daily_loss_halt_pct):
            risk = "BUYS-HALTED"
        try:
            from kill_switch import is_kill_switch_on
            if await is_kill_switch_on():
                risk = "KILL-SWITCH ON"
        except Exception:
            pass

        return f"{n_pos} open position(s) | day {daily_pnl:+.2%} | risk={risk}"
    except Exception as e:
        logger.warning(f"Portfolio status line failed: {e}")
        return "unavailable"


# ═══════════════════════════════════════════════════════════════════════════
#   MAIN DAILY REPORT
# ═══════════════════════════════════════════════════════════════════════════

async def generate_daily_report() -> str:
    """
    Generate and emit the daily one-page report.
    Returns the report string (also logged + sent to Telegram).
    """
    from config import settings
    from market_time import ist_today_str
    from kill_switch import is_kill_switch_on

    today_str = ist_today_str()
    kill_switch_on = await is_kill_switch_on()
    legacy_line = await _get_legacy_status_line()

    lines = [
        f"{'=' * 56}",
        f"  DAILY REPORT — {today_str}  (mode={settings.trading_mode})",
        f"{'=' * 56}",
        "",
        "── PORTFOLIO STATUS ────────────────────────────────",
        f"  {legacy_line}",
        f"  Kill switch: {'ON  ⛔ (manual clear required)' if kill_switch_on else 'OFF ✅'}",
        f"{'=' * 56}",
    ]

    report = "\n".join(lines)

    # ── Emit ─────────────────────────────────────────────────────────────
    logger.info("\n" + report)

    try:
        from telegram_bot import send_message
        tg_lines = [
            f"📊 *Daily Report — {today_str}*",
            f"💼 Portfolio: {legacy_line}",
            f"🔴 Kill switch: {'ON ⛔' if kill_switch_on else 'OFF ✅'}",
        ]
        import asyncio
        asyncio.create_task(send_message("\n".join(tg_lines)))
    except Exception as e:
        logger.debug(f"Telegram daily report failed (non-critical): {e}")

    return report


# ═══════════════════════════════════════════════════════════════════════════
#   WEEKLY SUMMARY (Sunday 18:00 IST job)
# ═══════════════════════════════════════════════════════════════════════════

def _as_naive_utc(value) -> datetime | None:
    """Normalize datetime/ISO-string/None → naive UTC datetime (or None)."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


async def build_weekly_summary(send_to_telegram: bool = True) -> str:
    """
    Weekly summary:
      - Hall-of-Fame count + any new additions this week
      - Portfolio status (positions, risk)
      - LLM/Gemma daily-budget usage
    Returns the text; the scheduler logs it and this sends it to Telegram.
    """
    from market_time import ist_today_str

    today_str = ist_today_str()

    # ── Research / Hall of Fame ──────────────────────────────────────────
    hof_note = "Hall of Fame: unavailable"
    try:
        from alpha_sandbox.hall_of_fame import list_hall_of_fame
        active = await list_hall_of_fame(limit=200)
        week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        new_names = []
        for h in active:
            promoted = _as_naive_utc(h.get("promoted_at"))
            if promoted is not None and promoted >= week_ago:
                new_names.append(str(h.get("name", "?")))
        if new_names:
            added = (f"; +{len(new_names)} this week "
                     f"({', '.join(new_names[:5])})")
        else:
            added = "; no new additions this week"
        hof_note = f"Hall of Fame: {len(active)} active{added}"
    except Exception as e:
        logger.warning(f"Weekly summary HOF block failed: {e}")

    # ── Legacy + LLM budget ──────────────────────────────────────────────
    legacy_line = await _get_legacy_status_line()
    llm_note = ""
    try:
        from llm_engine import get_daily_budget_status
        budget = get_daily_budget_status()
        llm_note = (f"LLM budget today: {budget.get('calls_today', '?')}"
                    f"/{budget.get('daily_limit', '?')}")
    except Exception as e:
        logger.warning(f"Weekly summary LLM budget failed: {e}")

    lines = [
        f"📅 Weekly Summary — {today_str}",
        f"🔬 {hof_note}",
        f"💼 Portfolio: {legacy_line}",
    ]
    if llm_note:
        lines.append(f"🤖 {llm_note}")

    summary = "\n".join(lines)

    if send_to_telegram:
        try:
            from telegram_bot import send_message
            import asyncio
            asyncio.create_task(send_message(summary))
        except Exception as e:
            logger.debug(f"Telegram weekly summary failed (non-critical): {e}")

    return summary
