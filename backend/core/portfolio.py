"""Pure portfolio math: cadence, vol target, rank buffer, planner, budgets, stops."""
from datetime import date
import numpy as np
import pandas as pd

def should_rebalance(last_str: str | None, today_str: str, cadence_days: int) -> bool:
    if not last_str:
        return True
    try:
        last = date.fromisoformat(last_str)
        today = date.fromisoformat(today_str)
    except ValueError:
        return True
    calendar_cadence = cadence_days * 7 // 5
    return (today - last).days >= calendar_cadence

def vol_target_scale(realized_ann_vol: float, target_ann_vol: float,
                     min_exposure: float, max_exposure: float) -> float:
    if realized_ann_vol <= 1e-9:
        return max_exposure
    return max(min_exposure, min(max_exposure, target_ann_vol / realized_ann_vol))

def composite_index(close_df: pd.DataFrame) -> pd.Series:
    norm = close_df.div(close_df.iloc[0])
    return norm.mean(axis=1, skipna=True)

def trend_overlay_on(idx: pd.Series, window: int) -> bool:
    sma = idx.rolling(window, min_periods=window).mean()
    return bool(idx.iloc[-1] > sma.iloc[-1])

def realized_vol(daily: pd.Series, lookback: int = 120) -> float:
    d = daily.dropna().tail(lookback)
    if len(d) < 60:
        return 0.0
    return float(d.std(ddof=1) * np.sqrt(252))

def select_names(ranked: list, held: set, enter_n: int = 25, exit_n: int = 35) -> list:
    """Rank buffer: enter at rank <= enter_n, exit only past exit_n.
    ranked[0] is rank 1."""
    out = []
    for i, t in enumerate(ranked, start=1):
        if t in held:
            if i <= exit_n:
                out.append(t)
        elif i <= enter_n:
            out.append(t)
    return out

def plan_rebalance_orders(holdings: list, target_tickers: list, prices: dict,
                          total_value: float, exposure: float, top_n: int,
                          band: float = 0.10) -> dict:
    """Pure planner with +/-band no-trade zone (ported from meta_strategy)."""
    target_per = (total_value * exposure / top_n) if top_n else 0.0
    held = {h["ticker"]: h for h in holdings if h.get("quantity", 0) > 0}
    sells, buys, skipped = [], [], []
    for t, h in held.items():
        if t not in target_tickers:
            sells.append({"ticker": t, "quantity": h["quantity"], "reason": "exit"})
    for t in target_tickers:
        p = prices.get(t)
        if not p or p <= 0:
            skipped.append({"ticker": t, "reason": "no_price"})
            continue
        cur_qty = held.get(t, {}).get("quantity", 0)
        cur_val = cur_qty * p
        if cur_val < target_per * (1 - band):
            qty = int((target_per - cur_val) // p)
            if qty >= 1:
                buys.append({"ticker": t, "quantity": qty})
            else:
                skipped.append({"ticker": t, "reason": "qty_rounds_to_zero"})
        elif cur_val > target_per * (1 + band):
            qty = int((cur_val - target_per) // p)
            if qty >= 1:
                sells.append({"ticker": t, "quantity": min(qty, cur_qty), "reason": "trim"})
    return {"sells": sells, "buys": buys, "skipped": skipped,
            "target_per": round(target_per, 2)}

def apply_turnover_budget(orders: list, prices: dict, book_value: float,
                          cap_frac: float = 0.30) -> tuple:
    """Turnover budget <= cap_frac of book per rebalance; proportional scale-down."""
    turnover = sum(o["quantity"] * prices.get(o["ticker"], 0.0) for o in orders)
    cap = book_value * cap_frac
    if turnover <= cap or turnover <= 0:
        return orders, False
    scale = cap / turnover
    scaled = []
    for o in orders:
        q = int(o["quantity"] * scale)
        if q >= 1:
            scaled.append({**o, "quantity": q})
    return scaled, True

def catastrophic_stops(holdings: list, prices: dict, stop_pct: float = 0.25) -> list:
    """Name-level -25% from avg cost, checked at daily MTM, code-only."""
    out = []
    for h in holdings:
        t = h.get("ticker")
        avg = h.get("avg_price", 0)
        p = prices.get(t, 0)
        if avg > 0 and p > 0 and p <= avg * (1 - stop_pct):
            out.append(t)
    return out

def swap_worth_it(rank_old: int, rank_new: int, min_delta: int = 5) -> bool:
    """Cost-aware skip: rank delta < 5 => no swap."""
    return abs(rank_old - rank_new) >= min_delta
