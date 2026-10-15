"""Stress windows (frozen: 2008 GFC, 2020 COVID, vol-spike) for every candidate."""
import numpy as np
import pandas as pd
from typing import Optional

WINDOWS = {
    "gfc_2008": ("2008-01-01", "2009-03-31"),
    "covid_2020": ("2020-02-15", "2020-06-30"),
}

def _window_stats(seg: pd.Series) -> dict:
    seg = seg.dropna()
    if len(seg) < 20:
        return {"days": int(len(seg)), "sharpe": None, "max_dd_pct": None}
    ann = float(seg.mean()) * 252
    vol = float(seg.std(ddof=1)) * np.sqrt(252)
    cum = (1.0 + seg).cumprod()
    dd = float((cum / cum.cummax() - 1.0).min()) * 100
    return {"days": int(len(seg)),
            "sharpe": round(ann / vol, 2) if vol > 0 else 0.0,
            "max_dd_pct": round(dd, 2)}

def vol_spike_mask(index_ret: pd.Series, lookback: int = 252,
                   pctile: float = 90.0) -> pd.Series:
    rv = index_ret.rolling(20).std(ddof=1)
    ref = rv.rolling(lookback, min_periods=60).quantile(pctile / 100.0)
    return (rv > ref).fillna(False)

def stress_report(daily_net: pd.Series, index_ret: Optional[pd.Series] = None) -> dict:
    out = {}
    for name, (a, b) in WINDOWS.items():
        try:
            seg = daily_net.loc[a:b]
        except TypeError:
            seg = pd.Series(dtype=float)
        out[name] = _window_stats(seg)
    if index_ret is not None:
        mask = vol_spike_mask(index_ret).reindex(daily_net.index, fill_value=False)
        out["vol_spike"] = _window_stats(daily_net[mask])
    else:
        out["vol_spike"] = {"days": 0, "sharpe": None, "max_dd_pct": None}
    return out
