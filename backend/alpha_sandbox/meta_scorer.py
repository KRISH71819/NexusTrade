"""
Meta-Scorer — walk-forward IC-weighted blend of the signal library (Phase 7).

No look-ahead, by construction:
- Weights at rebalance t are learned only from rebalances whose forward
  window has fully completed by t.
- Positions activate at t+1 (embargo), costs charged on turnover.
- Optional regime overlay: all-cash when the composite market index is
  below its 200-day SMA (the only realistic path to the DD gate).
"""
import logging

import numpy as np
import pandas as pd

from alpha_sandbox.sandbox import one_side_cost_rate

logger = logging.getLogger(__name__)


def cross_sectional_ic(signal_row: pd.Series, fwd_row: pd.Series) -> float:
    """Spearman rank correlation across tickers between signal and forward return."""
    pair = pd.concat([signal_row.rename("s"), fwd_row.rename("f")], axis=1).dropna()
    if len(pair) < 5:
        return float("nan")
    return float(pair["s"].rank().corr(pair["f"].rank()))


def blend_weights(ic_history: dict, min_obs: int = 12) -> tuple:
    """
    Weight = mean historical IC (floored at 0 — never invert a signal),
    normalized. Equal-weight until a signal has >= min_obs observations.
    Returns (normalized_weights, raw_mean_ics).
    """
    raw = {}
    for name, ics in ic_history.items():
        clean = [x for x in ics if x == x and -1.0 <= x <= 1.0][-36:]
        raw[name] = round(float(np.mean(clean)), 3) if len(clean) >= min_obs else 0.0
    pos = {k: max(0.0, v) for k, v in raw.items()}
    total = sum(pos.values())
    if total <= 1e-9:
        return {k: 1.0 / len(raw) for k in raw}, raw
    return {k: v / total for k, v in pos.items()}, raw


def market_regime_series(close_df: pd.DataFrame, sma_window: int = 200) -> pd.Series:
    """Composite market index (mean of normalized closes) vs its 200-day SMA."""
    norm = close_df.div(close_df.iloc[0])
    idx = norm.mean(axis=1, skipna=True)
    sma = idx.rolling(sma_window, min_periods=sma_window).mean()
    return (idx > sma).fillna(False)


def run_meta_backtest(
    signal_panels: dict,
    close_df: pd.DataFrame,
    top_n: int = 10,
    rebalance_days: int = 20,
    use_regime: bool = True,
):
    rate = one_side_cost_rate()
    dates = close_df.index
    n = len(dates)
    ret_df = close_df.pct_change().fillna(0.0)
    # Forward return earned by a position entered at t+1, held rebalance_days
    fwd = close_df.shift(-(rebalance_days + 1)).div(close_df.shift(-1)) - 1.0
    regime = (
        market_regime_series(close_df)
        if use_regime
        else pd.Series(True, index=dates)
    )

    reb_idx = [i for i in range(200, n - rebalance_days - 1, rebalance_days)]
    ic_history = {name: [] for name in signal_panels}
    pending = []  # (j, signal rows at j) awaiting completed forward windows
    daily_gross = pd.Series(0.0, index=dates)
    cost = pd.Series(0.0, index=dates)
    exposure_days = 0
    turnover_total = 0.0
    weight_rows = []
    prev_w = pd.Series(0.0, index=close_df.columns)

    for k, i in enumerate(reb_idx):
        t = dates[i]

        # 1) book IC observations whose forward window is now complete
        pending.append((i, {nm: sp.iloc[i] for nm, sp in signal_panels.items()}))
        while pending and pending[0][0] + rebalance_days + 1 <= i:
            j, rows = pending.pop(0)
            for nm, srow in rows.items():
                ic_history[nm].append(cross_sectional_ic(srow, fwd.iloc[j]))

        # 2) learn weights, build composite score
        weights, raw = blend_weights(ic_history)
        new_w = pd.Series(0.0, index=close_df.columns)
        if bool(regime.iloc[i]):
            ranks = pd.DataFrame(
                {nm: sp.iloc[i].rank(pct=True) for nm, sp in signal_panels.items()}
            )
            valid = ranks.notna().sum(axis=1) >= max(2, len(signal_panels) // 2)
            comp = ranks.mul(pd.Series(weights)).sum(axis=1, skipna=True).where(valid)
            picks = comp.dropna().sort_values(ascending=False).head(top_n).index
            new_w.loc[list(picks)] = 1.0 / top_n

        # 3) activate t+1 .. next rebalance; charge turnover cost at activation
        a = i + 1
        b = (reb_idx[k + 1] + 1) if k + 1 < len(reb_idx) else n
        if a < n:
            seg = slice(a, min(b, n))
            daily_gross.iloc[seg] = daily_gross.iloc[seg] + (
                ret_df.iloc[seg].mul(new_w.values, axis=1).sum(axis=1)
            ).values
            tv = float((new_w - prev_w).abs().sum())
            turnover_total += tv
            cost.iloc[a] += tv * rate
            if new_w.sum() > 0:
                exposure_days += (min(b, n) - a)
        prev_w = new_w

        weight_rows.append({
            "date": t,
            "regime_bull": bool(regime.iloc[i]),
            "weights": {k2: round(v2, 3) for k2, v2 in weights.items()},
            "raw_ic": raw,
            "n_picks": int((new_w > 0).sum()),
        })

    daily_net = (daily_gross - cost).fillna(0.0)
    info = {
        "tickers_used": int(close_df.notna().any().sum()),
        "days": n,
        "exposure_pct": round(exposure_days / n * 100, 1),
        "ann_turnover": round(turnover_total / (n / 252), 1),
        "one_side_cost_rate": round(rate, 5),
    }
    return daily_net, info, weight_rows


def _learn_ic_history(
    signal_panels: dict,
    close_df: pd.DataFrame,
    rebalance_days: int = 20,
) -> tuple:
    """Extract historical ICs across all completed rebalance forward windows."""
    dates = close_df.index
    n = len(dates)
    fwd = close_df.shift(-(rebalance_days + 1)).div(close_df.shift(-1)) - 1.0
    reb_idx = [i for i in range(200, n - rebalance_days - 1, rebalance_days)]
    ic_history = {name: [] for name in signal_panels}
    pending = []

    for i in reb_idx:
        pending.append((i, {nm: sp.iloc[i] for nm, sp in signal_panels.items()}))
        while pending and pending[0][0] + rebalance_days + 1 <= i:
            j, rows = pending.pop(0)
            for nm, srow in rows.items():
                ic_history[nm].append(cross_sectional_ic(srow, fwd.iloc[j]))

    # Also process any remaining pending windows that have completed by bar n-1
    while pending and pending[0][0] + rebalance_days + 1 <= n - 1:
        j, rows = pending.pop(0)
        for nm, srow in rows.items():
            ic_history[nm].append(cross_sectional_ic(srow, fwd.iloc[j]))

    return ic_history, reb_idx


def current_scores(panel: dict, rebalance_days: int = 20) -> tuple:
    """
    Using the SAME walk-forward IC machinery as the backtest, compute the
    blend weights from all completed rebalances, then score the LAST bar:
    composite = sum_s w_s * cross_sectional_rank_pct(signal_s, last bar).
    Returns (composite.dropna().sort_values(ascending=False), weights).
    """
    from alpha_sandbox.signal_library import build_signal_panels, close_panel

    close_df = close_panel(panel)
    signal_panels = build_signal_panels(panel)
    ic_history, _ = _learn_ic_history(signal_panels, close_df, rebalance_days=rebalance_days)
    weights, _ = blend_weights(ic_history)

    # Score the last bar (-1)
    ranks = pd.DataFrame(
        {nm: sp.iloc[-1].rank(pct=True) for nm, sp in signal_panels.items()}
    )
    valid = ranks.notna().sum(axis=1) >= max(2, len(signal_panels) // 2)
    comp = ranks.mul(pd.Series(weights)).sum(axis=1, skipna=True).where(valid)
    return comp.dropna().sort_values(ascending=False), weights


def strategy_recent_vol(
    panel: dict,
    top_n: int = 10,
    rebalance_days: int = 20,
    lookback_days: int = 252,
) -> float:
    """Annualized vol of the blend's daily net returns over the last year.
    MUST use the same code path as the live blend backtest."""
    from alpha_sandbox.signal_library import build_signal_panels, close_panel

    close_df = close_panel(panel)
    signal_panels = build_signal_panels(panel)
    result = run_meta_backtest(
        signal_panels,
        close_df,
        top_n=top_n,
        rebalance_days=rebalance_days,
        use_regime=False,
    )
    daily_net = result[0] if isinstance(result, tuple) else result
    d = daily_net.dropna().tail(lookback_days)
    if len(d) < 60:
        return 0.0
    return float(d.std(ddof=1) * (252 ** 0.5))
