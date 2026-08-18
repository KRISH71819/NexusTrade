"""
Alpha evaluation metrics + walk-forward stability + go/no-go gates (Phase 1).
"""
import logging

import numpy as np
import pandas as pd

from config import settings

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


def compute_metrics(daily_net: pd.Series) -> dict:
    d = daily_net.dropna()
    n = len(d)
    if n < 60:
        return {"days": n, "status": "insufficient_data"}
    growth = float((1.0 + d).prod())
    years = n / TRADING_DAYS
    ann_return = (growth ** (1.0 / years) - 1.0) if (years > 0 and growth > 0) else -1.0
    vol_daily = float(d.std(ddof=1))
    ann_vol = vol_daily * np.sqrt(TRADING_DAYS)
    sharpe = (ann_return / ann_vol) if ann_vol > 0 else 0.0
    cum = (1.0 + d).cumprod()
    max_dd = float((cum / cum.cummax() - 1.0).min())
    active = d[d != 0.0]
    win_rate = float((active > 0).mean()) if len(active) else 0.0
    return {
        "days": n,
        "ann_return_pct": round(ann_return * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "win_rate_pct": round(win_rate * 100, 1),
    }


def walk_forward_sharpes(daily_net: pd.Series, folds: int | None = None) -> list:
    folds = folds or settings.alpha_fold_count
    d = daily_net.dropna()
    if len(d) < 60 * folds:
        return []
    out = []
    for chunk in np.array_split(d, folds):
        m = compute_metrics(pd.Series(chunk))
        out.append(float(round(m.get("sharpe", 0.0), 2)))
    return out


def apply_gates(metrics: dict, fold_sharpes: list, bench_max_dd_pct: float | None = None) -> dict:
    sharpe_ok = bool(metrics.get("sharpe", -9.0) >= settings.alpha_gate_sharpe)
    dd_val = metrics.get("max_dd_pct", 999.0)
    if settings.alpha_gate_dd_mode == "absolute":
        dd_ok = bool(dd_val >= -settings.alpha_gate_max_dd * 100)
    else:
        # relative: both values are negative; alpha must be less negative
        # than alpha_gate_dd_relative * bench (e.g. -30 >= 0.75 * -40)
        if bench_max_dd_pct is None or bench_max_dd_pct >= 0:
            dd_ok = False
        else:
            dd_ok = bool(dd_val >= settings.alpha_gate_dd_relative * bench_max_dd_pct)
    stability_ok = bool(
        fold_sharpes
        and sum(1 for s in fold_sharpes if s > 0) / len(fold_sharpes)
        >= settings.alpha_gate_min_fold_win_rate
    )
    turnover_ok = bool(metrics.get("ann_turnover", 0.0) <= settings.alpha_max_annual_turnover)
    return {
        "sharpe": sharpe_ok,
        "max_dd": dd_ok,
        "stability": stability_ok,
        "turnover": turnover_ok,
        "all": sharpe_ok and dd_ok and stability_ok and turnover_ok,
    }
