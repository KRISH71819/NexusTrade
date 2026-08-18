"""
Daily one-page report (Batch 1.5).

Emits once per day (post-close) to:
  - Python logger (INFO level — always)
  - Telegram (if configured)

Report sections:
  1. Score distribution across holdings (min/p25/median/p75/max)
  2. Exit trigger histogram — count of each trigger, **including zeros**
     (a permanently-zero trigger is a dead code path to investigate)
  3. Realised friction paid today (₹ and % of portfolio)
  4. Day open, day close, daily P&L %
  5. Circuit-breaker state; LLM/ML failure count
  6. Portfolio return vs NIFTY 500 over the same window (most critical line)

Hard constraints honoured:
  - trading_mode stays "paper", dhan_trading_enabled stays False
  - No remote push; runs in the same process as the scheduler
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Benchmark symbols (in priority order) ───────────────────────────────────
_BENCHMARK_CANDIDATES = ["^CRSLDX", "^NSEI"]  # NIFTY 500, fallback NIFTY 50


# ═══════════════════════════════════════════════════════════════════════════════
#   BENCHMARK FETCH
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_benchmark_return(start_date: datetime) -> tuple[float | None, str]:
    """
    Fetch the cumulative return of the benchmark from *start_date* to today.

    Returns:
        (return_pct, label) — e.g. (0.0312, "NIFTY 500 (^CRSLDX)")
        or (None, "unavailable") on error.
    """
    try:
        import yfinance as yf

        end_date = datetime.now(timezone.utc) + timedelta(days=1)
        for symbol in _BENCHMARK_CANDIDATES:
            try:
                hist = yf.download(
                    symbol,
                    start=start_date.strftime("%Y-%m-%d"),
                    end=end_date.strftime("%Y-%m-%d"),
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                )
                if hist.empty or len(hist) < 2:
                    continue

                # Handle MultiIndex columns from yfinance
                close = hist["Close"]
                if hasattr(close, "columns"):
                    close = close.iloc[:, 0]

                first = float(close.iloc[0])
                last = float(close.iloc[-1])
                if first <= 0:
                    continue

                label = "NIFTY 500 (^CRSLDX)" if symbol == "^CRSLDX" else "NIFTY 50 (^NSEI) [fallback]"
                return round((last / first) - 1.0, 6), label
            except Exception as e:
                logger.debug(f"Benchmark fetch failed for {symbol}: {e}")
                continue
    except ImportError:
        logger.warning("yfinance not installed — benchmark unavailable")

    return None, "unavailable"


# ═══════════════════════════════════════════════════════════════════════════════
#   SCORE DISTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_score_distribution_today(ist_day_start_utc: datetime) -> dict:
    """
    Fetch today's score snapshots from MongoDB and compute distribution stats.
    Returns percentile dict or empty dict if no data yet.
    """
    try:
        from database import get_score_history_collection
        coll = get_score_history_collection()
        docs = await coll.find(
            {"timestamp": {"$gte": ist_day_start_utc}}
        ).to_list(length=1000)

        if not docs:
            return {}

        # Flatten all per-ticker scores across all cycles today
        all_scores: list[float] = []
        for doc in docs:
            all_scores.extend(doc.get("scores", {}).values())

        if not all_scores:
            return {}

        all_scores.sort()
        n = len(all_scores)

        def pct(p: float) -> float:
            idx = int(p * (n - 1))
            return round(all_scores[idx], 4)

        return {
            "min": round(all_scores[0], 4),
            "p25": pct(0.25),
            "median": pct(0.50),
            "p75": pct(0.75),
            "max": round(all_scores[-1], 4),
            "count": n,
            "cycles": len(docs),
        }
    except Exception as e:
        logger.warning(f"Could not fetch score distribution: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
#   TRIGGER HISTOGRAM (from trades today)
# ═══════════════════════════════════════════════════════════════════════════════

_ALL_TRIGGERS = [
    "stop_loss",
    "locked_stop",
    "trailing_stop_strict",
    "trailing_stop",
    "profit_taking_tier1",
    "profit_taking_tier2",
    "underperformer_bleed",
    "underperformer_stagnant",
    "underperformer_momentum",
    "crisis_full_sell",
    "score_reduction",       # Batch 2.5 (inert until then)
]


async def _get_trigger_histogram(ist_day_start_utc: datetime) -> dict[str, int]:
    """
    Count exit triggers fired today from the trades collection.
    Returns all known trigger names — including those with zero counts
    (a permanently-zero trigger is a dead code detector).
    """
    histogram: dict[str, int] = {t: 0 for t in _ALL_TRIGGERS}

    try:
        from database import get_trades_collection
        coll = get_trades_collection()
        docs = await coll.find(
            {"timestamp": {"$gte": ist_day_start_utc}, "action": "SELL"}
        ).to_list(length=5000)

        for doc in docs:
            reason: str = doc.get("action_reason", "")
            trigger: str | None = None

            # Match known trigger strings from risk_manager / scheduler
            if "STOP-LOSS" in reason:
                trigger = "stop_loss"
            elif "PROFIT LOCK HIT" in reason or "BREAK-EVEN STOP" in reason:
                trigger = "locked_stop"
            elif "TRAILING STOP HIT" in reason and "strict" in reason.lower():
                trigger = "trailing_stop_strict"
            elif "TRAILING STOP HIT" in reason:
                trigger = "trailing_stop"
            elif "PROFIT TIER 1" in reason:
                trigger = "profit_taking_tier1"
            elif "PROFIT TIER 2" in reason:
                trigger = "profit_taking_tier2"
            elif "SLOW BLEED" in reason:
                trigger = "underperformer_bleed"
            elif "STAGNANT" in reason:
                trigger = "underperformer_stagnant"
            elif "NEGATIVE MOMENTUM" in reason:
                trigger = "underperformer_momentum"
            elif "crisis" in reason.lower():
                trigger = "crisis_full_sell"
            elif "score" in reason.lower() and "reduction" in reason.lower():
                trigger = "score_reduction"

            if trigger:
                histogram[trigger] = histogram.get(trigger, 0) + 1

    except Exception as e:
        logger.warning(f"Could not build trigger histogram: {e}")

    return histogram


# ═══════════════════════════════════════════════════════════════════════════════
#   FRICTION CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_friction_today(ist_day_start_utc: datetime, total_value: float) -> dict:
    """
    Sum all charges paid today from the trades collection.
    """
    result = {"total_charges_inr": 0.0, "total_charges_pct": 0.0, "trade_count": 0}
    try:
        from database import get_trades_collection
        coll = get_trades_collection()
        docs = await coll.find(
            {"timestamp": {"$gte": ist_day_start_utc}}
        ).to_list(length=5000)

        total = sum(
            doc.get("charges", {}).get("total_charges", 0.0) for doc in docs
        )
        result["total_charges_inr"] = round(total, 2)
        result["trade_count"] = len(docs)
        if total_value > 0:
            result["total_charges_pct"] = round(total / total_value * 100, 4)
    except Exception as e:
        logger.warning(f"Could not sum friction: {e}")
    return result


async def _get_meta_section_lines() -> list:
    try:
        from config import settings
        from database import get_meta_portfolio_collection, get_meta_equity_collection
        doc = await get_meta_portfolio_collection().find_one({"_id": "meta"}, {"_id": 0})
        if not doc:
            return []
        total = doc.get("total_value", 0)
        initial = settings.meta_initial_capital
        since = ((total / initial) - 1.0) if initial > 0 else 0.0
        hv = doc.get("holdings_value", 0) or sum(h.get("market_value", 0) for h in doc.get("holdings", []))
        tv = doc.get("total_value", 0) or 1
        ex_actual = hv / tv
        target_exp = doc.get("strat_info", {}).get("exposure", doc.get("exposure_scale", 1.0))
        eq = await get_meta_equity_collection().find(
            {}, {"_id": 0, "total_value": 1}).sort("timestamp", -1).limit(2).to_list(length=2)
        daily_pct = 0.0
        if len(eq) == 2 and eq[-1].get("total_value", 0) > 0:
            daily_pct = (eq[0]["total_value"] / eq[-1]["total_value"]) - 1.0
        n_hold = len([h for h in doc.get("holdings", []) if h.get("quantity", 0) > 0])
        return [
            "",
            "── META RESEARCH PORTFOLIO (isolated paper book) ─────",
            f"  Value: Rs.{total:>12,.2f}  ({since:+.2%} since inception)",
            f"  Day: {daily_pct:+.2%} | Exposure: {ex_actual:.0%} "
            f"(target {target_exp:.0%}) | Holdings: {n_hold}",
            f"  Last rebalance: {doc.get('last_rebalance', 'n/a')}",
        ]
    except Exception as e:
        logger.warning(f"Meta report section failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN REPORT
# ═══════════════════════════════════════════════════════════════════════════════

async def generate_daily_report() -> str:
    """
    Generate and emit the daily one-page report.
    Returns the report string (also logged + sent to Telegram).
    """
    from config import settings
    from market_time import ist_day_start_utc, ist_today_str
    from kill_switch import is_kill_switch_on

    now_utc = datetime.now(timezone.utc)
    day_start_utc = ist_day_start_utc(now_utc)
    today_str = ist_today_str()

    # ── Portfolio state ───────────────────────────────────────────────────────
    try:
        from ledger import get_portfolio_for_mode, get_daily_pnl_pct
        portfolio = await get_portfolio_for_mode(settings.trading_mode)
        total_value = portfolio.get("total_value", 0.0)
        day_open = portfolio.get("day_open_value", total_value)
        initial_balance = portfolio.get("initial_balance", settings.initial_balance)
        daily_pnl_pct = await get_daily_pnl_pct(settings.trading_mode)
    except Exception as e:
        logger.error(f"Daily report: could not fetch portfolio: {e}")
        total_value = day_open = initial_balance = 0.0
        daily_pnl_pct = 0.0

    kill_switch_on = await is_kill_switch_on()

    # ── Overall P&L from inception ────────────────────────────────────────────
    inception_pnl_pct = (
        (total_value / initial_balance) - 1.0 if initial_balance > 0 else 0.0
    )

    # ── Score distribution ───────────────────────────────────────────────────
    score_dist = await _get_score_distribution_today(day_start_utc)

    # ── Trigger histogram ────────────────────────────────────────────────────
    trigger_hist = await _get_trigger_histogram(day_start_utc)

    # ── Friction ──────────────────────────────────────────────────────────────
    friction = await _get_friction_today(day_start_utc, total_value)

    # ── NIFTY 500 benchmark ───────────────────────────────────────────────────
    # Use inception date as start so the bot vs. benchmark comparison is on the
    # same window. Fall back to 30 days if inception date unavailable.
    try:
        inception_str = portfolio.get("created_at")
        if inception_str and isinstance(inception_str, datetime):
            bench_start = inception_str
        elif inception_str and isinstance(inception_str, str):
            bench_start = datetime.fromisoformat(inception_str.replace("Z", "+00:00"))
        else:
            bench_start = now_utc - timedelta(days=30)
    except Exception:
        bench_start = now_utc - timedelta(days=30)

    bench_return, bench_label = _fetch_benchmark_return(bench_start)
    alpha = (
        round(inception_pnl_pct - bench_return, 6)
        if bench_return is not None
        else None
    )

    # ── Format report string ─────────────────────────────────────────────────
    lines = [
        f"{'=' * 56}",
        f"  DAILY REPORT — {today_str}  (mode={settings.trading_mode})",
        f"{'=' * 56}",
        "",
        "── PORTFOLIO ──────────────────────────────────────────",
        f"  Day open:   Rs.{day_open:>12,.2f}",
        f"  Day close:  Rs.{total_value:>12,.2f}",
        f"  Daily P&L:  {daily_pnl_pct:+.2%}",
        f"  Since open: Rs.{initial_balance:,.2f}  ({inception_pnl_pct:+.2%})",
        "",
        "── BENCHMARK ──────────────────────────────────────────",
    ]
    if bench_return is not None:
        lines += [
            f"  {bench_label}: {bench_return:+.2%}",
            f"  Bot vs. benchmark alpha: {alpha:+.2%}",
            f"  (window: {bench_start.strftime('%Y-%m-%d')} → {today_str})",
        ]
    else:
        lines.append("  Benchmark: unavailable (yfinance error)")

    lines += [
        "",
        "── SCORE DISTRIBUTION (held stocks, today) ────────────",
    ]
    if score_dist:
        lines += [
            f"  min={score_dist['min']:.3f}  p25={score_dist['p25']:.3f}  "
            f"median={score_dist['median']:.3f}  p75={score_dist['p75']:.3f}  "
            f"max={score_dist['max']:.3f}",
            f"  ({score_dist['count']} data points across {score_dist['cycles']} cycle(s))",
        ]
    else:
        lines.append("  No score data yet today.")

    lines += [
        "",
        "── EXIT TRIGGER HISTOGRAM ─────────────────────────────",
        "  (a permanently-zero trigger = dead code path)",
    ]
    for trigger, count in sorted(trigger_hist.items()):
        star = " ⚠️" if count == 0 else ""
        lines.append(f"  {trigger:<30s} {count:>4}{star}")

    lines += [
        "",
        "── FRICTION (costs today) ─────────────────────────────",
        f"  Total charges: Rs.{friction['total_charges_inr']:,.2f}  "
        f"({friction['total_charges_pct']:.4f}% of portfolio)",
        f"  Trades today:  {friction['trade_count']}",
    ]

    # Meta research portfolio section
    meta_lines = await _get_meta_section_lines()
    if meta_lines:
        lines += meta_lines

    lines += [
        "",
        "── SYSTEM STATE ───────────────────────────────────────",
        f"  Kill switch:      {'ON  ⛔ (manual clear required)' if kill_switch_on else 'OFF ✅'}",
        f"  Trading mode:     {settings.trading_mode}",
        f"  Dhan live trade:  {'ENABLED ⚠️' if settings.dhan_trading_enabled else 'disabled'}",
        f"{'=' * 56}",
    ]

    report = "\n".join(lines)

    # ── Emit ─────────────────────────────────────────────────────────────────
    logger.info("\n" + report)

    try:
        from telegram_bot import send_message
        # Telegram uses Markdown; send a condensed version
        tg_lines = [
            f"📊 *Daily Report — {today_str}*",
            f"Mode: `{settings.trading_mode}`",
            "",
            f"💰 Day P&L: *{daily_pnl_pct:+.2%}*",
            f"📈 Since open: *{inception_pnl_pct:+.2%}*",
        ]
        if bench_return is not None:
            tg_lines += [
                f"🏦 {bench_label}: *{bench_return:+.2%}*",
                f"⚡ Alpha: *{alpha:+.2%}*",
            ]
        if score_dist:
            tg_lines.append(
                f"📊 Score dist: min={score_dist['min']:.2f} / "
                f"med={score_dist['median']:.2f} / max={score_dist['max']:.2f}"
            )
        tg_lines += [
            f"💸 Friction today: Rs.{friction['total_charges_inr']:,.0f}  "
            f"({friction['total_charges_pct']:.3f}%)",
            f"🔴 Kill switch: {'ON ⛔' if kill_switch_on else 'OFF ✅'}",
        ]
        import asyncio
        asyncio.create_task(send_message("\n".join(tg_lines)))
    except Exception as e:
        logger.debug(f"Telegram daily report failed (non-critical): {e}")

    return report
