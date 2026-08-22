"""Meta research portfolio endpoints — visibility + manual trigger."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from database import (
    get_meta_portfolio_collection,
    get_meta_trades_collection,
    get_meta_equity_collection,
)
from meta_portfolio import rebalance_meta_portfolio

router = APIRouter(prefix="/meta", tags=["Meta Research"])

SYSTEM_B_BADGE = ("SYSTEM B — VALIDATED CONFIG: trend_200 rank | top-25 | "
                  "60d rebalance | trend overlay | vol target 15%")


@router.get("/status")
async def meta_status():
    doc = await get_meta_portfolio_collection().find_one({"_id": "meta"}, {"_id": 0})
    trades = await get_meta_trades_collection().find(
        {}, {"_id": 0}).sort("timestamp", -1).limit(15).to_list(length=15)
    equity = await get_meta_equity_collection().find(
        {}, {"_id": 0}).sort("timestamp", -1).limit(30).to_list(length=30)
    return {"portfolio": doc, "recent_trades": trades, "equity": list(reversed(equity))}


@router.get("/equity")
async def meta_equity():
    docs = await get_meta_equity_collection().find(
        {}, {"_id": 0}).sort("timestamp", 1).to_list(length=500)
    return {"equity": docs, "count": len(docs)}


@router.post("/rebalance")
async def meta_rebalance():
    return await rebalance_meta_portfolio(force=True)


# ── Helpers (pure, unit-testable) ─────────────────────────────────────────────

def replay_realized_pnl(trades: list) -> float:
    """
    Avg-cost replay of the meta trade log → net realized P&L in INR
    (includes per-trade charges, since charges are part of the trade docs).
    Pure function; trades sorted by timestamp ascending inside.
    """
    books: dict = {}  # ticker -> {qty: int, cost: float}
    realized = 0.0
    for t in sorted(trades, key=lambda d: d.get("timestamp") or datetime.min.replace(tzinfo=timezone.utc)):
        side = str(t.get("action", "")).upper()
        qty = int(t.get("quantity", 0) or 0)
        price = float(t.get("price", 0.0) or 0.0)
        if qty <= 0 or price <= 0:
            continue
        ticker = t.get("ticker", "")
        charges = float((t.get("charges") or {}).get("total_charges", 0.0) or 0.0)
        book = books.setdefault(ticker, {"qty": 0, "cost": 0.0})
        if side == "BUY":
            book["qty"] += qty
            book["cost"] += qty * price + charges
        elif side == "SELL":
            if book["qty"] <= 0:
                continue  # sell without known basis — ignore defensively
            avg = book["cost"] / book["qty"]
            closed = min(qty, book["qty"])
            realized += (closed * price - charges) - avg * closed
            book["qty"] -= closed
            book["cost"] -= avg * closed
    return round(realized, 2)


def next_rebalance_info(last_rebalance: str | None, cadence_days: int,
                        today_str: str) -> dict:
    """
    Next rebalance date + countdown. Trading-day cadence converts to calendar
    via ceil(cadence * 7 / 5) — mirrors meta_portfolio.should_rebalance().
    If never rebalanced, the next scheduled job fires immediately.
    """
    from datetime import date

    calendar_days = -(-cadence_days * 7 // 5)  # ceil division
    if not last_rebalance:
        return {"next_rebalance_date": today_str or None, "days_until_rebalance": 0}
    try:
        last = date.fromisoformat(str(last_rebalance)[:10])
        today = date.fromisoformat(today_str) if today_str else date.today()
    except ValueError:
        return {"next_rebalance_date": None, "days_until_rebalance": None}
    due = last.fromordinal(last.toordinal() + calendar_days)
    days_left = max(0, (due - today).days)
    return {"next_rebalance_date": due.isoformat(), "days_until_rebalance": days_left}


def pnl_vs_history(equity_docs: list, latest_value: float) -> dict:
    """
    Daily and weekly P&L from the equity snapshot series
    ([{date, timestamp, total_value}, ...] ascending).
    Daily: vs the most recent snapshot on an EARLIER date than the latest.
    Weekly: vs the OLDEST snapshot within the trailing 7 calendar days of the
    latest snapshot's date; falls back to the first available snapshot.
    """
    out = {"daily_pnl": 0.0, "daily_pnl_pct": 0.0,
           "weekly_pnl": 0.0, "weekly_pnl_pct": 0.0}
    if not equity_docs or latest_value <= 0:
        return out

    latest_date = str(equity_docs[-1].get("date", ""))[:10]

    def pct(base: float) -> tuple:
        if base <= 0:
            return 0.0, 0.0
        diff = latest_value - base
        return round(diff, 2), round(diff / base, 6)

    # ── daily ─────────────────────────────────────────────────────────────
    prev = None
    for snap in reversed(equity_docs[:-1]):
        if str(snap.get("date", ""))[:10] != latest_date:
            prev = float(snap.get("total_value", 0.0))
            break
    if prev is not None:
        out["daily_pnl"], out["daily_pnl_pct"] = pct(prev)

    # ── weekly ────────────────────────────────────────────────────────────
    try:
        from datetime import date as _date
        cutoff = (_date.fromisoformat(latest_date) - timedelta(days=7)).isoformat()
    except ValueError:
        return out
    window = [s for s in equity_docs if str(s.get("date", ""))[:10] >= cutoff]
    if window:
        base = float(window[0].get("total_value", 0.0))
        out["weekly_pnl"], out["weekly_pnl_pct"] = pct(base)
    return out


@router.get("/summary")
async def meta_summary():
    """One-call numeric summary powering the System B dashboard page."""
    from config import settings
    from kill_switch import is_kill_switch_on
    from market_time import ist_today_str

    doc = await get_meta_portfolio_collection().find_one({"_id": "meta"}, {"_id": 0})
    if not doc:
        return {"status": "no_portfolio",
                "badge": SYSTEM_B_BADGE}

    total = float(doc.get("total_value", 0.0) or 0.0)
    initial = float(settings.meta_initial_capital or 0.0)

    holdings = [h for h in doc.get("holdings", []) if h.get("quantity", 0) > 0]
    holdings_value = sum(
        float(h.get("market_value", 0.0) or 0.0)
        or float(h.get("quantity", 0)) * float(h.get("avg_price", 0.0) or 0.0)
        for h in holdings
    )
    exposure_actual = (holdings_value / total) if total > 0 else 0.0

    # Unrealized: market value vs cost basis of open positions.
    unrealized = sum(
        (float(h.get("current_price", 0.0) or 0.0) or float(h.get("avg_price", 0.0)))
        * float(h.get("quantity", 0))
        - float(h.get("avg_price", 0.0) or 0.0) * float(h.get("quantity", 0))
        for h in holdings
    )

    trades = await get_meta_trades_collection().find(
        {}, {"_id": 0}
    ).sort("timestamp", 1).to_list(length=5000)
    realized = replay_realized_pnl(trades)

    equity_docs = await get_meta_equity_collection().find(
        {}, {"_id": 0, "date": 1, "timestamp": 1, "total_value": 1}
    ).sort("timestamp", 1).to_list(length=1000)
    pnl = pnl_vs_history(equity_docs, total)

    strat_info = doc.get("strat_info") or {}
    recon = doc.get("reconciliation") or {}
    rebalance = next_rebalance_info(
        doc.get("last_rebalance"), settings.meta_rebalance_days, ist_today_str()
    )
    kill_switch_on = await is_kill_switch_on()

    return {
        "status": "ok",
        "badge": SYSTEM_B_BADGE,
        "total_value": round(total, 2),
        "initial_capital": initial,
        "since_inception_pct": round(total / initial - 1.0, 6) if initial > 0 else 0.0,
        **pnl,
        "realized_pnl": realized,
        "unrealized_pnl": round(unrealized, 2),
        "cash": round(float(doc.get("cash", 0.0) or 0.0), 2),
        "holdings_value": round(holdings_value, 2),
        "exposure_actual": round(exposure_actual, 4),
        "exposure_target": recon.get("target_exposure",
                                     strat_info.get("exposure")),
        "vol_scale": strat_info.get("vol_scale"),
        "trend_on": strat_info.get("trend_on"),
        "realized_vol": strat_info.get("realized_vol"),
        "holdings_count": len(holdings),
        "names_target": recon.get("names_target"),
        "last_rebalance": doc.get("last_rebalance"),
        **rebalance,
        "kill_switch_active": bool(kill_switch_on),
        "peak_value": doc.get("peak_value"),
    }
