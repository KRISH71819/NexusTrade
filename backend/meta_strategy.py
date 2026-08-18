"""
Meta Strategy — the validated Phase-12 configuration.
trend_200 cross-sectional rank, top-25, 60d rebalance,
+ market 200d trend overlay + 15% vol targeting.
Pure read-only: returns target weights + an explainability info dict.
"""
import numpy as np
import pandas as pd

from config import settings
from alpha_sandbox.dsl import evaluate_expression


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


def vol_scale(realized: float, target: float, floor: float, cap: float) -> float:
    if realized <= 1e-9:
        return cap
    return max(floor, min(cap, target / realized))


def compute_target_weights(panel: dict, close_df: pd.DataFrame):
    """
    Returns (weights: {ticker: weight}, info: dict).
    weights sum to the current exposure (0..1); empty dict = full cash.
    """
    # ── 1. Stock-level cross-sectional signal (trend_200) ────────────
    scores = {}
    for ticker, df in panel.items():
        try:
            indexed = df.set_index("date").sort_index()
            s = evaluate_expression(settings.meta_signal_expr, indexed)
            v = s.dropna()
            if len(v):
                scores[ticker] = float(v.iloc[-1])
        except Exception:
            continue
    if not scores:
        return {}, {"reason": "no valid signals"}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    picks = [t for t, _ in ranked[: settings.meta_top_n]]

    # ── 2. Market regime overlay (composite vs 200d MA) ──────────────
    idx = composite_index(close_df)
    on = (not settings.meta_use_trend_overlay) or trend_overlay_on(
        idx, settings.meta_trend_window
    )

    # ── 3. Vol targeting (proxy: composite daily returns) ────────────
    rv = realized_vol(idx.pct_change())
    vs = (
        vol_scale(rv, settings.meta_target_ann_vol,
                  settings.meta_vol_floor, settings.meta_vol_cap)
        if settings.meta_use_vol_target else 1.0
    )

    exposure = (1.0 if on else 0.0) * vs
    n = len(picks)
    weights = {t: exposure / n for t in picks} if (n and exposure > 0) else {}

    info = {
        "signal": settings.meta_signal_expr,
        "trend_on": bool(on),
        "realized_vol": round(rv, 4),
        "vol_scale": round(vs, 3),
        "exposure": round(exposure, 3),
        "top_n": n,
        "rebalance_days": settings.meta_rebalance_days,
    }
    return weights, info


def plan_rebalance_orders(
    holdings: list,          # [{"ticker", "quantity"}]
    target_tickers: list,    # intended names (len == top_n)
    prices: dict,            # {ticker: price}
    total_value: float,
    exposure: float,         # 0..1
    top_n: int,
    band: float = 0.10,      # no-trade band ±10% around target weight
) -> dict:
    """Pure rebalance planner. Returns sells/buys/skipped with reasons."""
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
