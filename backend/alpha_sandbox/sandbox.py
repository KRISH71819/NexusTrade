"""
Alpha Sandbox — offline backtester for DSL alphas (Phase 1).

v2 fixes:
- Signal is evaluated on the DATE-INDEXED frame so signal, positions and
  returns share one index (v1 misaligned positions vs returns).
- Daily returns clipped to ±20% (NSE circuit limit) so pre-2005
  unadjusted corporate-action artifacts in yfinance "max" history
  cannot poison portfolio metrics.

OFFLINE ONLY: never import from main.py / scheduler.py.
"""
import logging

import numpy as np
import pandas as pd

from config import settings
from ledger import calculate_trade_charges
from history_store import get_ohlcv
from alpha_sandbox.dsl import evaluate_expression

logger = logging.getLogger(__name__)

MAX_DAILY_MOVE = 0.20  # NSE single-stock circuit cap; beyond this = data artifact


def one_side_cost_rate(reference_turnover: float = 100_000.0) -> float:
    """Average one-side charge rate + slippage, from the LIVE cost model."""
    buy = calculate_trade_charges(reference_turnover, "BUY")["total_charges"] / reference_turnover
    sell = calculate_trade_charges(reference_turnover, "SELL")["total_charges"] / reference_turnover
    return (buy + sell) / 2.0 + settings.slippage_bps / 10_000.0


async def load_history_panel(tickers: list, min_rows: int | None = None, start=None) -> dict:
    min_rows = min_rows or settings.alpha_min_history_days
    panel = {}
    for ticker in tickers:
        try:
            df = await get_ohlcv(ticker, start=start)
        except Exception as e:
            logger.warning(f"{ticker}: history read failed: {e}")
            continue
        if df is None or len(df) < min_rows:
            logger.warning(f"{ticker}: insufficient history — skipped")
            continue
        panel[ticker] = df.reset_index(drop=True)
    return panel


def _apply_position_rules(want: pd.Series, cadence_days: int, min_hold_days: int) -> pd.Series:
    """Stateful position construction: entries/exits only on cadence days;
    exits blocked until min_hold_days elapsed. Kills whipsaw turnover."""
    if cadence_days <= 1 and min_hold_days <= 0:
        return want
    w = want.fillna(0.0).to_numpy(dtype=float)
    out = np.zeros(len(w))
    cur = 0.0
    bars_in_pos = 0
    bars_since_dec = cadence_days  # force a decision on the first bar
    for i in range(len(w)):
        if bars_since_dec >= cadence_days:
            bars_since_dec = 0
            if cur == 0.0 and w[i] > 0:
                cur, bars_in_pos = 1.0, 0
            elif cur == 1.0 and w[i] == 0.0 and bars_in_pos >= min_hold_days:
                cur = 0.0
        bars_since_dec += 1
        if cur == 1.0:
            bars_in_pos += 1
        out[i] = cur
    return pd.Series(out, index=want.index)


def backtest_signal(
    panel: dict,
    expression: str,
    embargo: int | None = None,
    cadence_days: int | None = None,
    min_hold_days: int | None = None,
    portfolio_rule: str | None = None,
    top_n: int | None = None,
    rebalance_days: int | None = None,
    use_trend_overlay: bool = True,
    trend_window: int = 200,
    use_vol_target: bool = True,
    target_ann_vol: float = 0.15,
):
    """
    Backtest of one DSL alpha.
    Modes:
      - mode='top_n' (default for candidates): long equal-weight top-N by score (N=25),
        rebalance 60d, with 200d market trend overlay + 15% vol targeting (identical
        to live System B meta portfolio).
      - mode='sign': long all stocks where score > 0 (kept for centered expressions
        and buy&hold benchmark 'close / close').
    Returns (daily_net_returns, info).
    """
    embargo = settings.alpha_embargo_days if embargo is None else embargo
    cadence_days = settings.alpha_default_cadence if cadence_days is None else cadence_days
    min_hold_days = settings.alpha_default_min_hold if min_hold_days is None else min_hold_days
    rate = one_side_cost_rate()

    # Determine mode: default candidates to top_n; 'close / close' benchmark defaults to sign
    rule = portfolio_rule or getattr(settings, "alpha_default_portfolio_rule", "top_n")
    if expression.strip() == "close / close":
        rule = "sign"

    if rule == "sign":
        ret_frames, pos_frames, turn_frames = {}, {}, {}
        clipped_outliers = 0
        for ticker, raw in panel.items():
            try:
                df = raw.set_index("date").sort_index()
                signal = evaluate_expression(expression, df)
            except Exception as e:
                logger.warning(f"{ticker}: expression failed: {e}")
                continue
            close = df["close"].astype(float)
            ret = close.pct_change()
            clipped_outliers += int(((ret > MAX_DAILY_MOVE) | (ret < -MAX_DAILY_MOVE)).sum())
            ret = ret.clip(-MAX_DAILY_MOVE, MAX_DAILY_MOVE)
            pos = (signal.fillna(0.0) > 0).astype(float).shift(embargo).fillna(0.0)
            pos = _apply_position_rules(pos, cadence_days, min_hold_days)
            ret_frames[ticker] = ret
            pos_frames[ticker] = pos
            turn_frames[ticker] = pos.diff().abs().fillna(pos.abs())

        if not ret_frames:
            return pd.Series(dtype=float), {"tickers_used": 0}

        ret_df = pd.DataFrame(ret_frames).fillna(0.0)
        pos_df = pd.DataFrame(pos_frames).fillna(0.0)
        turn_df = pd.DataFrame(turn_frames).fillna(0.0)

        active = pos_df.sum(axis=1)
        gross = (ret_df * pos_df).sum(axis=1)
        turn = turn_df.sum(axis=1)

        mask = active > 0
        daily_gross = pd.Series(0.0, index=ret_df.index)
        daily_turn = pd.Series(0.0, index=ret_df.index)
        daily_gross[mask] = gross[mask] / active[mask]
        daily_turn[mask] = turn[mask] / active[mask]
        daily_net = (daily_gross - daily_turn * rate).fillna(0.0)

        universe_size = len(ret_frames)
        avg_names_held = round(float(active[mask].mean()) if mask.any() else 0.0, 2)
        exposure_mean = round(float(mask.mean()), 4)
        benchmark_clone = bool(avg_names_held > 0.9 * universe_size)

        info = {
            "tickers_used": universe_size,
            "days": int(len(daily_net)),
            "exposure_pct": round(float(mask.mean()) * 100, 1),
            "exposure_mean": exposure_mean,
            "avg_names_held": avg_names_held,
            "benchmark_clone": benchmark_clone,
            "one_side_cost_rate": round(rate, 5),
            "clipped_outlier_days": clipped_outliers,
            "ann_turnover": round(float(daily_turn.sum() / (len(daily_net) / 252)), 1),
            "cadence_days": cadence_days,
            "min_hold_days": min_hold_days,
            "portfolio_rule": "sign",
        }
        return daily_net, info

    # ── mode="top_n" (identical construction to incumbent live meta book) ─────
    top_n_val = top_n if top_n is not None else settings.meta_top_n
    rb_days = rebalance_days if rebalance_days is not None else settings.meta_rebalance_days

    signal_frames, close_frames = {}, {}
    clipped_outliers = 0
    for ticker, raw in panel.items():
        try:
            df = raw.set_index("date").sort_index()
            signal = evaluate_expression(expression, df)
            close = df["close"].astype(float)
            signal_frames[ticker] = signal
            close_frames[ticker] = close
        except Exception as e:
            logger.warning(f"{ticker}: top_n eval failed: {e}")
            continue

    if not close_frames:
        return pd.Series(dtype=float), {"tickers_used": 0}

    close_df = pd.DataFrame(close_frames).sort_index()
    signal_df = pd.DataFrame(signal_frames).reindex(close_df.index)
    ret_df = close_df.pct_change()
    clipped_outliers = int(((ret_df > MAX_DAILY_MOVE) | (ret_df < -MAX_DAILY_MOVE)).sum().sum())
    ret_df = ret_df.clip(-MAX_DAILY_MOVE, MAX_DAILY_MOVE).fillna(0.0)
    n = len(close_df)
    universe_size = close_df.shape[1]

    # 1. Market composite index (equal-weighted normalized price)
    norm = close_df.div(close_df.iloc[0])
    composite = norm.mean(axis=1, skipna=True)

    # 2. Trend overlay (200d SMA on composite)
    if use_trend_overlay:
        sma = composite.rolling(trend_window, min_periods=min(60, n)).mean()
        trend_on = (composite > sma).fillna(False)
    else:
        trend_on = pd.Series(True, index=close_df.index)

    # 3. Vol targeting (120d lookback realized vol, target=15%)
    if use_vol_target:
        daily_comp_ret = composite.pct_change()
        rv = daily_comp_ret.rolling(120, min_periods=min(60, n)).std(ddof=1) * np.sqrt(252)
        vs = (target_ann_vol / rv).clip(settings.meta_vol_floor, settings.meta_vol_cap).fillna(1.0)
    else:
        vs = pd.Series(1.0, index=close_df.index)

    exposure_series = trend_on.astype(float) * vs

    # 4. Top-N portfolio weights with 60d rebalance cadence
    weight_df = pd.DataFrame(0.0, index=close_df.index, columns=close_df.columns)
    warmup = min(60, max(0, n - 1))
    rb = list(range(warmup + embargo, n - embargo, rb_days))
    if not rb and n > embargo + 1:
        rb = [embargo + 1]

    base_weights = pd.DataFrame(0.0, index=close_df.index, columns=close_df.columns)
    for k, i in enumerate(rb):
        scores = signal_df.iloc[i].dropna()
        w = pd.Series(0.0, index=close_df.columns)
        if len(scores):
            picks = scores.sort_values(ascending=False).head(top_n_val).index
            w[picks] = 1.0 / len(picks)
        next_i = rb[k + 1] if k + 1 < len(rb) else n
        a = i + embargo + 1
        b = min(next_i + embargo + 1, n)
        if a >= n:
            break
        base_weights.iloc[a:b] = w.values

    # Scale base weights by the daily exposure series
    weight_df = base_weights.mul(exposure_series.reindex(base_weights.index).fillna(0.0), axis=0)

    # 5. Turnover and net returns
    turn = weight_df.diff().abs().sum(axis=1)
    turn.iloc[0] = weight_df.abs().sum(axis=1).iloc[0]
    cost = (turn * rate).fillna(0.0)
    daily_gross = (weight_df * ret_df).sum(axis=1)
    daily_net = (daily_gross - cost).fillna(0.0)

    names_held_daily = (weight_df.abs() > 1e-6).sum(axis=1)
    active_days_mask = names_held_daily > 0
    avg_names_held = round(float(names_held_daily[active_days_mask].mean()) if active_days_mask.any() else 0.0, 2)
    exposure_mean = round(float(weight_df.sum(axis=1).mean()), 4)
    benchmark_clone = bool(avg_names_held > 0.9 * universe_size)

    info = {
        "tickers_used": universe_size,
        "days": n,
        "exposure_pct": round(float(active_days_mask.mean()) * 100, 1),
        "exposure_mean": exposure_mean,
        "avg_names_held": avg_names_held,
        "benchmark_clone": benchmark_clone,
        "ann_turnover": round(float(turn.sum() / (n / 252)), 1) if n > 0 else 0.0,
        "top_n": top_n_val,
        "rebalance_days": rb_days,
        "one_side_cost_rate": round(rate, 5),
        "clipped_outlier_days": clipped_outliers,
        "portfolio_rule": "top_n",
    }
    return daily_net, info


def backtest_ranking_from_signal(
    signal_df, close_df, top_n=10, rebalance_days=20, embargo=None, exposure=None
):
    """Top-N ranking backtest from a PRE-COMPUTED signal panel
    (index=dates, columns=tickers). Shared engine for the breadth study."""
    embargo = settings.alpha_embargo_days if embargo is None else embargo
    rate = one_side_cost_rate()
    close_df = close_df.sort_index()
    signal_df = signal_df.reindex(close_df.index)
    ret_df = close_df.pct_change().fillna(0.0)
    n = len(close_df)

    weight_df = pd.DataFrame(0.0, index=close_df.index, columns=close_df.columns)
    rb = list(range(60 + embargo, n - embargo, rebalance_days))
    for k, i in enumerate(rb):
        scores = signal_df.iloc[i].dropna()
        w = pd.Series(0.0, index=close_df.columns)
        if len(scores):
            picks = scores.sort_values(ascending=False).head(top_n).index
            w[picks] = 1.0 / len(picks)
        next_i = rb[k + 1] if k + 1 < len(rb) else n
        a = i + embargo + 1
        b = min(next_i + embargo + 1, n)
        if a >= n:
            break
        weight_df.iloc[a:b] = w.values

    if exposure is not None:
        weight_df = weight_df.mul(
            exposure.reindex(weight_df.index).fillna(1.0), axis=0
        )

    turn = weight_df.diff().abs().sum(axis=1)
    turn.iloc[0] = weight_df.abs().sum(axis=1).iloc[0]
    cost = (turn * rate).fillna(0.0)
    daily_gross = (weight_df * ret_df).sum(axis=1)
    daily_net = (daily_gross - cost).fillna(0.0)

    info = {
        "tickers_used": int(close_df.shape[1]),
        "days": n,
        "exposure_pct": round(float((weight_df.abs().sum(axis=1) > 0).mean()) * 100, 1),
        "ann_turnover": round(float(turn.sum() / (n / 252)), 1),
        "top_n": top_n,
        "rebalance_days": rebalance_days,
        "one_side_cost_rate": round(rate, 5),
    }
    return daily_net, info


def backtest_ranking(panel, expression, top_n=10, rebalance_days=20, embargo=None):
    frames = {}
    for ticker, raw in panel.items():
        df = raw.set_index("date").sort_index()
        try:
            frames[ticker] = evaluate_expression(expression, df)
        except Exception as e:
            logger.warning(f"{ticker}: expression failed: {e}")
    if not frames:
        return pd.Series(dtype=float), {"tickers_used": 0}
    signal_df = pd.DataFrame(frames)
    close_df = pd.DataFrame(
        {t: raw.set_index("date")["close"].astype(float) for t, raw in panel.items()}
    )
    return backtest_ranking_from_signal(signal_df, close_df, top_n, rebalance_days, embargo)
