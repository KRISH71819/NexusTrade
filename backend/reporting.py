"""
Daily one-page report (Section 3 — System B switchover).

Emits once per day (post-close, 15:35 IST) to:
  - Python logger (INFO level — always)
  - Telegram (if configured)

Report layout (META-primary):
  1. SYSTEM B — meta research portfolio: value, daily P&L, exposure vs target,
     vol scale, holdings count, next rebalance countdown
  2. ONE line of legacy summary (open positions count + risk status)

Also provides build_weekly_summary() for the Sunday 18:00 IST job:
meta weekly P&L / exposure / vol scale, Hall-of-Fame count + new additions,
legacy book status, and LLM daily-budget usage.

Hard constraints honoured:
  - trading_mode stays "paper", dhan_trading_enabled stays False
  - No remote push; runs in the same process as the scheduler
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#   SYSTEM B — META PRIMARY BLOCK (daily report)
# ═══════════════════════════════════════════════════════════════════════════

async def _get_meta_primary_lines() -> list:
    """
    SYSTEM B primary block for the daily report: value, since-inception %,
    daily P&L, exposure vs target, vol scale, holdings count, next rebalance.
    Returns [] when the meta book does not exist yet.
    """
    try:
        from config import settings
        from market_time import ist_today_str
        from database import (
            get_meta_portfolio_collection,
            get_meta_equity_collection,
        )
        from routers.meta import next_rebalance_info, pnl_vs_history

        doc = await get_meta_portfolio_collection().find_one({"_id": "meta"}, {"_id": 0})
        if not doc:
            return []

        total = float(doc.get("total_value", 0.0) or 0.0)
        initial = float(settings.meta_initial_capital or 0.0)
        since = (total / initial - 1.0) if initial > 0 else 0.0

        eq_docs = await get_meta_equity_collection().find(
            {}, {"_id": 0, "date": 1, "timestamp": 1, "total_value": 1}
        ).sort("timestamp", 1).to_list(length=1000)
        pnl = pnl_vs_history(eq_docs, total)

        strat = doc.get("strat_info") or {}
        recon = doc.get("reconciliation") or {}
        hv = float(doc.get("holdings_value", 0.0) or 0.0) or sum(
            h.get("market_value", 0) for h in doc.get("holdings", [])
        )
        ex_actual = (hv / total) if total > 0 else 0.0

        target = recon.get("target_exposure")
        if target is None:
            target = strat.get("exposure")
        target_txt = f"{target:.0%}" if isinstance(target, (int, float)) else "?"

        vs = strat.get("vol_scale")
        vs_txt = f"x{vs}" if isinstance(vs, (int, float)) else "?"
        trend_note = "" if strat.get("trend_on") in (True, None) else " | TREND OVERLAY OFF"

        n_hold = len([h for h in doc.get("holdings", []) if h.get("quantity", 0) > 0])
        rb = next_rebalance_info(
            doc.get("last_rebalance"), settings.meta_rebalance_days, ist_today_str()
        )

        return [
            f"  Value: Rs.{total:>12,.2f}  ({since:+.2%} since inception)",
            f"  Day P&L: {pnl['daily_pnl']:+,.2f} ({pnl['daily_pnl_pct']:+.2%})",
            f"  Exposure: {ex_actual:.0%} (target {target_txt}) "
            f"| Vol scale: {vs_txt}{trend_note}",
            f"  Holdings: {n_hold}/{recon.get('names_target', '?')} | "
            f"Next rebalance: {rb.get('next_rebalance_date') or 'n/a'} "
            f"(in {rb.get('days_until_rebalance', '?')}d)",
        ]
    except Exception as e:
        logger.warning(f"Meta primary report section failed: {e}")
        return []


async def _get_legacy_status_line() -> str:
    """ONE-line legacy summary: open positions count + risk state."""
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

        tag = "" if settings.legacy_engine_enabled else " [FROZEN]"
        return f"{n_pos} open position(s) | day {daily_pnl:+.2%} | risk={risk}{tag}"
    except Exception as e:
        logger.warning(f"Legacy status line failed: {e}")
        return "unavailable"


# ═══════════════════════════════════════════════════════════════════════════
#   MAIN DAILY REPORT
# ═══════════════════════════════════════════════════════════════════════════

async def generate_daily_report() -> str:
    """
    Generate and emit the daily one-page report (META-primary, Section 3.1).
    Returns the report string (also logged + sent to Telegram).
    """
    from config import settings
    from market_time import ist_today_str
    from kill_switch import is_kill_switch_on

    today_str = ist_today_str()
    kill_switch_on = await is_kill_switch_on()

    meta_lines = await _get_meta_primary_lines()
    legacy_line = await _get_legacy_status_line()

    lines = [
        f"{'=' * 56}",
        f"  DAILY REPORT — {today_str}  (mode={settings.trading_mode})",
        f"{'=' * 56}",
        "",
        "── SYSTEM B — META RESEARCH PORTFOLIO ──────────────",
    ]
    lines += meta_lines if meta_lines else [
        "  Meta book unavailable (not seeded yet)."
    ]

    lines += [
        "",
        "── LEGACY (SYSTEM A) ───────────────────────────────",
        f"  {legacy_line}",
        f"  Kill switch: {'ON  ⛔ (manual clear required)' if kill_switch_on else 'OFF ✅'}",
        f"{'=' * 56}",
    ]

    report = "\n".join(lines)

    # ── Emit ─────────────────────────────────────────────────────────────
    logger.info("\n" + report)

    try:
        from telegram_bot import send_message
        tg_lines = [f"📊 *Daily Report — {today_str}*"]
        tg_lines += [ln.strip() for ln in meta_lines]
        tg_lines.append(f"🧊 Legacy: {legacy_line}")
        tg_lines.append(f"🔴 Kill switch: {'ON ⛔' if kill_switch_on else 'OFF ✅'}")
        import asyncio
        asyncio.create_task(send_message("\n".join(tg_lines)))
    except Exception as e:
        logger.debug(f"Telegram daily report failed (non-critical): {e}")

    return report


# ═══════════════════════════════════════════════════════════════════════════
#   WEEKLY SUMMARY (Sunday 18:00 IST job — Section 3.3)
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
    Weekly summary (Section 3.3):
      - meta book weekly P&L, exposure, vol scale
      - Hall-of-Fame count + any new additions this week
      - legacy book status (positions, risk)
      - LLM/Gemma daily-budget usage
    Returns the text; the scheduler logs it and this sends it to Telegram.
    """
    from config import settings
    from market_time import ist_today_str
    from database import (
        get_meta_portfolio_collection,
        get_meta_equity_collection,
    )
    from routers.meta import next_rebalance_info, pnl_vs_history

    today_str = ist_today_str()

    # ── SYSTEM B ─────────────────────────────────────────────────────────
    meta_note = "System B: unavailable"
    try:
        doc = await get_meta_portfolio_collection().find_one({"_id": "meta"}, {"_id": 0})
        if doc:
            total = float(doc.get("total_value", 0.0) or 0.0)
            initial = float(settings.meta_initial_capital or 0.0)
            since = (total / initial - 1.0) if initial > 0 else 0.0
            eq_docs = await get_meta_equity_collection().find(
                {}, {"_id": 0, "date": 1, "timestamp": 1, "total_value": 1}
            ).sort("timestamp", 1).to_list(length=1000)
            pnl = pnl_vs_history(eq_docs, total)
            strat = doc.get("strat_info") or {}
            recon = doc.get("reconciliation") or {}
            hv = float(doc.get("holdings_value", 0.0) or 0.0)
            ex_actual = (hv / total) if total > 0 else 0.0
            target = recon.get("target_exposure")
            if target is None:
                target = strat.get("exposure")
            n_hold = len([h for h in doc.get("holdings", [])
                          if h.get("quantity", 0) > 0])
            rb = next_rebalance_info(doc.get("last_rebalance"),
                                     settings.meta_rebalance_days, today_str)
            vs = strat.get("vol_scale")
            target_txt = f"{target:.0%}" if isinstance(target, (int, float)) else "?"
            vs_txt = f"x{vs}" if isinstance(vs, (int, float)) else "?"
            meta_note = (
                f"*System B:* Rs.{total:,.0f} ({since:+.1%} inception) | Week P&L "
                f"*{pnl['weekly_pnl']:+,.0f}* ({pnl['weekly_pnl_pct']:+.2%})\n"
                f"Exposure {ex_actual:.0%}/{target_txt} | Vol {vs_txt} | "
                f"Holdings {n_hold} | Rebalance in {rb.get('days_until_rebalance', '?')}d"
            )
    except Exception as e:
        logger.warning(f"Weekly summary meta block failed: {e}")

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
        meta_note,
        f"🔬 {hof_note}",
        f"🧊 Legacy: {legacy_line}",
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
