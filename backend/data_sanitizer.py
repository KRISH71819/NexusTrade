"""
Data Sanitizer (Phase 2.5) — NSE equity daily OHLCV bar validator & cleaner.
Protects the backtest sandbox and live pipelines from yfinance artifacts:
- Inverted bars (High < Low)
- Zero/NaN volume suspension days
- Non-positive prices
- Unadjusted corporate action spikes/crashes (|ret| > 30%)
"""
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

MAX_NORMAL_DAILY_RETURN = 0.30  # NSE circuit breakers are max 20%; >30% = split/data anomaly

def clean_daily_bars(df: pd.DataFrame, ticker: str = "UNKNOWN") -> pd.DataFrame:
    """
    Sanitizes daily OHLCV bars for NSE equities.
    1. Enforces OHLC logic (High >= Low, High >= max(O, C), Low <= min(O, C)).
    2. Drops zero-volume / NaN-volume bars (trading suspensions).
    3. Drops non-positive prices.
    4. Drops extreme unadjusted corporate action gaps (>30% move).
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df

    initial_len = len(df)
    clean_df = df.copy()

    # Case-insensitive column matching
    col_map = {c: c.lower() for c in clean_df.columns}
    clean_df = clean_df.rename(columns=col_map)

    required_cols = {"open", "high", "low", "close", "volume"}
    if not required_cols.issubset(clean_df.columns):
        logger.warning(f"{ticker}: Missing required OHLCV columns — returning as-is")
        return df

    # 1. Enforce basic OHLC integrity
    mask_inverted = clean_df["high"] < clean_df["low"]
    if mask_inverted.any():
        logger.warning(f"{ticker}: Fixing {int(mask_inverted.sum())} inverted bars (high < low)")
        clean_df.loc[mask_inverted, ["high", "low"]] = clean_df.loc[mask_inverted, ["low", "high"]].values

    clean_df["high"] = clean_df[["open", "close", "high"]].max(axis=1)
    clean_df["low"] = clean_df[["open", "close", "low"]].min(axis=1)

    # 2. Drop zero-volume or NaN-volume days (stock suspensions/untraded)
    mask_zero_vol = (clean_df["volume"] <= 0) | clean_df["volume"].isna()
    if mask_zero_vol.any():
        clean_df = clean_df[~mask_zero_vol]

    # 3. Drop invalid (non-positive) prices
    mask_invalid_price = (clean_df["close"] <= 0) | (clean_df["low"] <= 0) | (clean_df["open"] <= 0) | (clean_df["high"] <= 0)
    if mask_invalid_price.any():
        clean_df = clean_df[~mask_invalid_price]

    # 4. Filter unadjusted corporate action anomalies (>30% gap)
    if len(clean_df) > 1:
        rets = clean_df["close"].pct_change()
        mask_extreme_gap = rets.abs() > MAX_NORMAL_DAILY_RETURN
        if mask_extreme_gap.any():
            logger.warning(f"{ticker}: Dropping {int(mask_extreme_gap.sum())} extreme gap days (>30% move, likely bad corp action adjust)")
            clean_df = clean_df[~mask_extreme_gap]

    dropped = initial_len - len(clean_df)
    if dropped > 0:
        logger.info(f"{ticker}: Sanitizer removed {dropped} bad bars ({initial_len} -> {len(clean_df)})")

    return clean_df.reset_index(drop=True) if "date" in clean_df.columns else clean_df
