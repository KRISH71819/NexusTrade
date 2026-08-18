"""
Meta Research Portfolio — paper-only execution of the Phase-7 meta-scorer.
Separate capital + trade log so attribution never mixes with the main LLM
pipeline. Costs identical to live (ledger.calculate_trade_charges + config
slippage). Every trade doc carries the score/weight breakdown for
explainability. Flag-gated (settings.meta_portfolio_enabled), never live.
"""
import asyncio
import logging
from datetime import datetime, timezone, date

from config import settings
from database import (
    get_meta_portfolio_collection,
    get_meta_trades_collection,
    get_meta_equity_collection,
)
from data_ingestion import get_batch_prices
from ledger import calculate_trade_charges
from history_store import get_ohlcv
from market_time import ist_today_str
from alpha_sandbox import meta_scorer
import fetch_history

logger = logging.getLogger(__name__)

DEFAULT_META_UNIVERSE = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS",
    "KOTAKBANK.NS", "BAJFINANCE.NS", "TCS.NS", "INFY.NS", "HCLTECH.NS",
    "WIPRO.NS", "TECHM.NS", "SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS",
    "MARUTI.NS", "TATAMOTORS.NS", "EICHERMOT.NS", "LT.NS", "ULTRACEMCO.NS",
    "GRASIM.NS", "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "TITAN.NS",
    "ASIANPAINT.NS", "BHARTIARTL.NS", "ONGC.NS", "NTPC.NS", "COALINDIA.NS",
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "POWERGRID.NS",
    "TRENT.NS", "DMART.NS", "PIDILITIND.NS", "NAUKRI.NS", "ZOMATO.NS",
]


# ═══════════════════════ PURE CORE (unit-tested) ═══════════════════════
def should_rebalance(last_str: str | None, today_str: str, cadence_days: int) -> bool:
    if not last_str:
        return True
    try:
        last = date.fromisoformat(last_str)
        today = date.fromisoformat(today_str)
    except ValueError:
        return True
    calendar_cadence = cadence_days * 7 // 5  # trading days → calendar days
    return (today - last).days >= calendar_cadence


def vol_target_scale(
    realized_ann_vol: float,
    target_ann_vol: float,
    min_exposure: float,
    max_exposure: float,
) -> float:
    """Exposure scale so realized vol ≈ target. Pure, unit-tested."""
    if realized_ann_vol <= 1e-9:
        return max_exposure
    return max(min_exposure, min(max_exposure, target_ann_vol / realized_ann_vol))


def build_rebalance_orders(
    holdings: list,
    target_tickers: list,
    prices: dict,
    cash: float,
    total_value: float,
    drift_band: float = 0.3,
    invested_capital: float | None = None,
) -> list:
    """Pure order builder: sells exits, buys/adjusts entries, cash-constrained."""
    orders = []
    n = len(target_tickers)
    cap = invested_capital if invested_capital is not None else total_value
    if n == 0 or cap <= 0:
        return orders
    target_per = cap / n
    held = {h["ticker"]: h for h in holdings if h.get("quantity", 0) > 0}

    for t, h in held.items():                      # full exits first
        if t not in target_tickers and prices.get(t):
            orders.append({"ticker": t, "side": "SELL",
                           "quantity": h["quantity"], "price": prices[t]})

    cash_available = cash
    for t in target_tickers:                       # entries + drift adjusts
        p = prices.get(t)
        if not p or p <= 0:
            continue
        h = held.get(t)
        cur_val = h["quantity"] * p if h else 0.0
        if cur_val == 0.0:
            qty = int(min(target_per, cash_available) / p)
            if qty <= 0:
                continue
            orders.append({"ticker": t, "side": "BUY", "quantity": qty, "price": p})
            cash_available -= qty * p
        elif abs(cur_val - target_per) > drift_band * target_per:
            if cur_val > target_per:
                sell_qty = min(h["quantity"], int((cur_val - target_per) / p))
                if sell_qty > 0:
                    orders.append({"ticker": t, "side": "SELL",
                                   "quantity": sell_qty, "price": p})
                    cash_available += sell_qty * p
            else:
                buy_qty = int((target_per - cur_val) / p)
                cost = buy_qty * p
                if buy_qty > 0 and cost <= cash_available:
                    orders.append({"ticker": t, "side": "BUY",
                                   "quantity": buy_qty, "price": p})
                    cash_available -= cost
    return orders


# ═══════════════════════ EXECUTION (paper only) ═══════════════════════
async def _get_or_seed() -> dict:
    coll = get_meta_portfolio_collection()
    doc = await coll.find_one({"_id": "meta"})
    if doc is None:
        doc = {
            "_id": "meta",
            "cash": settings.meta_initial_capital,
            "holdings": [],
            "total_value": settings.meta_initial_capital,
            "peak_value": settings.meta_initial_capital,
            "last_rebalance": None,
            "created_at": datetime.now(timezone.utc),
        }
        await coll.insert_one(doc)
    return doc


async def mark_to_market() -> dict:
    """Daily live valuation of the meta book + idempotent equity snapshot."""
    coll = get_meta_portfolio_collection()
    doc = await coll.find_one({"_id": "meta"})
    if doc is None:
        return {"status": "no_portfolio"}

    holdings = doc.get("holdings", [])
    tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]
    prices = await asyncio.to_thread(get_batch_prices, tickers) if tickers else {}

    holdings_value = 0.0
    for h in holdings:
        p = prices.get(h["ticker"]) or h.get("avg_price", 0)
        h["current_price"] = round(p, 2)
        h["market_value"] = round(p * h.get("quantity", 0), 2)
        holdings_value += h["market_value"]

    total_value = round(doc.get("cash", 0) + holdings_value, 2)
    await coll.update_one(
        {"_id": "meta"},
        {"$set": {
            "holdings": holdings,
            "holdings_value": round(holdings_value, 2),
            "total_value": total_value,
            "updated_at": datetime.now(timezone.utc),
        }},
    )

    today = ist_today_str()
    eq = get_meta_equity_collection()
    if await eq.find_one({"date": today}) is None:
        await eq.insert_one({
            "date": today,
            "timestamp": datetime.now(timezone.utc),
            "total_value": total_value,
        })
    return {"status": "marked", "date": today, "total_value": total_value}


async def _execute_order(order: dict, doc: dict, breakdown: dict) -> None:
    slip = 1 + (settings.slippage_bps / 10_000) if order["side"] == "BUY" \
        else 1 - (settings.slippage_bps / 10_000)
    price = round(order["price"] * slip, 2)
    turnover = order["quantity"] * price
    charges = calculate_trade_charges(turnover, order["side"])

    if order["side"] == "SELL":
        doc["cash"] += turnover - charges["total_charges"]
        for h in doc["holdings"]:
            if h["ticker"] == order["ticker"]:
                h["quantity"] -= order["quantity"]
        doc["holdings"] = [h for h in doc["holdings"] if h["quantity"] > 0]
    else:
        doc["cash"] -= turnover + charges["total_charges"]
        existing = next((h for h in doc["holdings"]
                         if h["ticker"] == order["ticker"]), None)
        if existing:
            tot = existing["quantity"] + order["quantity"]
            existing["avg_price"] = round(
                (existing["avg_price"] * existing["quantity"] + price * order["quantity"]) / tot, 2)
            existing["quantity"] = tot
        else:
            doc["holdings"].append({"ticker": order["ticker"],
                                    "quantity": order["quantity"],
                                    "avg_price": price})

    trade = {
        "timestamp": datetime.now(timezone.utc),
        "ticker": order["ticker"],
        "action": order["side"],
        "quantity": order["quantity"],
        "price": price,
        "charges": charges,
        "breakdown": breakdown,          # explainability: weights + scores
    }
    await get_meta_trades_collection().insert_one(trade)
    logger.info(f"[META] {order['side']} {order['quantity']}x {order['ticker']} @ {price}")


async def rebalance_meta_portfolio(force: bool = False) -> dict:
    if not settings.meta_portfolio_enabled and not force:
        return {"status": "disabled"}
    doc = await _get_or_seed()
    today = ist_today_str()
    if not force and not should_rebalance(doc.get("last_rebalance"), today,
                                          settings.meta_rebalance_days):
        return {"status": "skip_cadence"}

    # 1) universe: seeded tickers in MongoDB, fallback to DEFAULT_META_UNIVERSE
    try:
        from database import get_db
        coll = get_db()["ohlcv_history"]
        cursor = await coll.aggregate([
            {"$group": {"_id": "$ticker", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gte": 500}}},
        ])
        docs = await cursor.to_list(length=1000) if hasattr(cursor, "to_list") else [d async for d in cursor]
        universe = sorted(d["_id"] for d in docs) if docs else DEFAULT_META_UNIVERSE
    except Exception:
        universe = DEFAULT_META_UNIVERSE

    # 2) panel load in parallel chunks (2010+ research window)
    start_dt = datetime(2010, 1, 1, tzinfo=timezone.utc)
    panel = {}
    for i in range(0, len(universe), 15):
        chunk = universe[i : i + 15]
        results = await asyncio.gather(*[get_ohlcv(t, start=start_dt) for t in chunk])
        for t, df in zip(chunk, results):
            if df is not None and not df.empty and len(df) >= 200:
                panel[t] = df

    if len(panel) < 10:
        return {"status": "insufficient_history", "panel": len(panel)}

    # 3) Phase-13 validated strategy & pure planner
    from meta_strategy import compute_target_weights, plan_rebalance_orders
    from alpha_sandbox.signal_library import close_panel
    from data_ingestion import get_latest_price

    close_df = close_panel(panel)
    weights, strat_info = compute_target_weights(panel, close_df)
    target = list(weights.keys())
    exposure = strat_info.get("exposure", 1.0)

    held = [h["ticker"] for h in doc["holdings"] if h.get("quantity", 0) > 0]
    needed_tickers = list(set(target + held))
    prices = await asyncio.to_thread(get_batch_prices, needed_tickers) if needed_tickers else {}
    for t in needed_tickers:
        if not prices.get(t) or prices[t] <= 0:
            p = await asyncio.to_thread(get_latest_price, t)
            if p and p > 0:
                prices[t] = p
            elif t in panel and not panel[t].empty:
                prices[t] = float(panel[t]["close"].iloc[-1])

    plan = plan_rebalance_orders(
        doc["holdings"], target, prices, doc["total_value"], exposure, len(target)
    )

    exec_skips = []
    n_orders = 0
    breakdown = {
        "strat_info": strat_info,
        "weights": weights,
        "exposure": round(exposure, 3),
    }

    # 1) Execute ALL sells first (exits + trims) to free cash
    for s in plan["sells"]:
        p = prices.get(s["ticker"]) or next(
            (h.get("avg_price", 0) for h in doc["holdings"] if h["ticker"] == s["ticker"]), 0
        )
        if p > 0 and s["quantity"] > 0:
            order = {"ticker": s["ticker"], "side": "SELL", "quantity": s["quantity"], "price": p}
            await _execute_order(order, doc, breakdown)
            n_orders += 1

    # 2) Execute buys in order; check cash before each
    buys_halted = doc["total_value"] < doc["peak_value"] * (1 - settings.meta_max_dd_halt)
    if buys_halted:
        logger.warning("[META] drawdown halt — buys blocked this rebalance")
    else:
        for b in plan["buys"]:
            p = prices.get(b["ticker"])
            if not p or p <= 0:
                exec_skips.append({"ticker": b["ticker"], "reason": "no_price"})
                continue
            qty = b["quantity"]
            if qty < 1:
                continue
            slip = 1 + (settings.slippage_bps / 10_000)
            est_price = round(p * slip, 2)
            turnover = qty * est_price
            charges = calculate_trade_charges(turnover, "BUY")
            cost = turnover + charges["total_charges"]
            if cost > doc["cash"]:
                max_qty = int(doc["cash"] // (est_price * 1.006))
                if max_qty >= 1:
                    qty = max_qty
                    turnover = qty * est_price
                    charges = calculate_trade_charges(turnover, "BUY")
                    cost = turnover + charges["total_charges"]
                if cost > doc["cash"] or qty < 1:
                    logger.warning(
                        f"[META] insufficient cash for {b['ticker']}: need {cost:.2f}, have {doc['cash']:.2f}"
                    )
                    exec_skips.append({"ticker": b["ticker"], "reason": "insufficient_cash"})
                    continue
            order = {"ticker": b["ticker"], "side": "BUY", "quantity": qty, "price": p}
            await _execute_order(order, doc, breakdown)
            n_orders += 1

    # 4) Revalue + store reconciliation record
    held_tickers = [h["ticker"] for h in doc["holdings"] if h.get("quantity", 0) > 0]
    live_prices = await asyncio.to_thread(get_batch_prices, held_tickers) if held_tickers else {}
    holdings_value = 0.0
    for h in doc["holdings"]:
        p = live_prices.get(h["ticker"]) or prices.get(h["ticker"]) or h.get("avg_price", 0)
        h["current_price"] = round(p, 2)
        h["market_value"] = round(p * h["quantity"], 2)
        holdings_value += h["market_value"]

    doc["holdings_value"] = round(holdings_value, 2)
    doc["total_value"] = round(doc["cash"] + holdings_value, 2)
    doc["peak_value"] = max(doc["peak_value"], doc["total_value"])
    doc["last_rebalance"] = today
    doc["exposure_scale"] = round(exposure, 3)
    doc["realized_vol"] = strat_info.get("realized_vol", 0.0)
    doc["strat_info"] = strat_info

    n_held = len([h for h in doc["holdings"] if h.get("quantity", 0) > 0])
    actual_exp = round(holdings_value / doc["total_value"], 3) if doc["total_value"] > 0 else 0.0
    doc["reconciliation"] = {
        "target_exposure": round(exposure, 3),
        "actual_exposure": actual_exp,
        "names_target": len(target),
        "names_held": n_held,
        "skipped": plan["skipped"] + exec_skips,
        "orders": n_orders,
    }

    await get_meta_portfolio_collection().replace_one({"_id": "meta"}, doc)
    await get_meta_equity_collection().insert_one({
        "date": today,
        "timestamp": datetime.now(timezone.utc),
        "total_value": doc["total_value"],
    })
    gap = actual_exp - exposure
    logger.info(
        f"[META] rebalance done | target_exp={exposure:.1%} actual_exp={actual_exp:.1%} "
        f"(gap={gap:+.1%}) | held={n_held}/{len(target)} | "
        f"orders={n_orders} | skips={len(plan['skipped'] + exec_skips)}"
    )
    return {
        "status": "rebalanced",
        "orders": n_orders,
        "total_value": doc["total_value"],
        "target": target,
        "exposure_scale": round(exposure, 3),
        "strat_info": strat_info,
        "reconciliation": doc["reconciliation"],
    }
