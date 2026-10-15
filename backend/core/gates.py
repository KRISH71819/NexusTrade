"""Metrics + walk-forward + go/no-go gates (ported evaluator math, v2 itemized gates)."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252
GATE_SHARPE = 1.0
GATE_DD_RELATIVE = 0.75     # alpha maxDD must be <= 75% of benchmark maxDD
GATE_TURNOVER = 24.0        # hard gate, x/yr
TARGET_TURNOVER = 12.0      # generator target, x/yr
GATE_MIN_FOLD_WIN = 0.5     # fraction of folds with Sharpe > 0
DEFAULT_FOLDS = 3

def compute_metrics(daily_net: pd.Series) -> dict:
    d = daily_net.dropna()
    n = len(d)
    if n < 60:
        return {"days": n, "status": "insufficient_data"}
    growth = float((1.0 + d).prod())
    years = n / TRADING_DAYS
    ann_return = (growth ** (1.0 / years) - 1.0) if (years > 0 and growth > 0) else -1.0
    ann_vol = float(d.std(ddof=1)) * np.sqrt(TRADING_DAYS)
    sharpe = (ann_return / ann_vol) if ann_vol > 0 else 0.0
    cum = (1.0 + d).cumprod()
    max_dd = float((cum / cum.cummax() - 1.0).min())
    active = d[d != 0.0]
    win_rate = float((active > 0).mean()) if len(active) else 0.0
    return {
        "days": n, "ann_return_pct": round(ann_return * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2), "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd * 100, 2), "win_rate_pct": round(win_rate * 100, 1),
    }

def walk_forward_sharpes(daily_net: pd.Series, folds: int | None = None) -> list:
    folds = folds or DEFAULT_FOLDS
    d = daily_net.dropna()
    if len(d) < 60 * folds:
        return []
    return [float(round(compute_metrics(pd.Series(c)).get("sharpe", 0.0), 2))
            for c in np.array_split(d, folds)]

def apply_gates(metrics: dict, fold_sharpes: list, bench_max_dd_pct: float | None = None,
                benchmark_clone: bool = False, universe_ok: bool = True,
                turnover_gate: float = GATE_TURNOVER) -> dict:
    """Itemized gates. `all` is True only if EVERY single gate is True."""
    sharpe_ok = bool(metrics.get("sharpe", -9.0) >= GATE_SHARPE)
    dd_val = metrics.get("max_dd_pct", 999.0)
    if bench_max_dd_pct is None or bench_max_dd_pct >= 0:
        dd_ok = False
    else:
        dd_ok = bool(dd_val >= GATE_DD_RELATIVE * bench_max_dd_pct)
    stability_ok = bool(fold_sharpes and
                        sum(1 for s in fold_sharpes if s > 0) / len(fold_sharpes) >= GATE_MIN_FOLD_WIN)
    turnover_ok = bool(metrics.get("ann_turnover", 0.0) <= turnover_gate)
    clone_ok = not bool(benchmark_clone)
    universe_ok = bool(universe_ok)
    return {
        "sharpe": sharpe_ok, "max_dd": dd_ok, "stability": stability_ok,
        "turnover": turnover_ok, "clone": clone_ok, "universe": universe_ok,
        "all": sharpe_ok and dd_ok and stability_ok and turnover_ok and clone_ok and universe_ok,
    }
