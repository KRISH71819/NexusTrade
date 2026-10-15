"""
Unit tests for data_sanitizer.py.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd
import numpy as np
from data_sanitizer import clean_daily_bars

def test_clean_daily_bars_inversion_and_bounds():
    # Bar 0: High < Low (inverted: high=90, low=110)
    # Bar 1: Normal
    df = pd.DataFrame({
        "open": [100.0, 102.0],
        "high": [90.0, 105.0],
        "low": [110.0, 101.0],
        "close": [95.0, 103.0],
        "volume": [100000, 150000],
    })
    cleaned = clean_daily_bars(df, "TEST.NS")
    assert len(cleaned) == 2
    assert cleaned.iloc[0]["high"] >= cleaned.iloc[0]["low"]
    assert cleaned.iloc[0]["high"] >= max(cleaned.iloc[0]["open"], cleaned.iloc[0]["close"])
    assert cleaned.iloc[0]["low"] <= min(cleaned.iloc[0]["open"], cleaned.iloc[0]["close"])

def test_clean_daily_bars_drops_zero_volume_and_invalid_prices():
    df = pd.DataFrame({
        "open": [100.0, 101.0, 102.0, 103.0],
        "high": [105.0, 105.0, 105.0, 105.0],
        "low": [95.0, 95.0, -1.0, 95.0],       # row 2 has negative low
        "close": [102.0, 102.0, 102.0, 102.0],
        "volume": [10000, 0, 10000, np.nan],   # rows 1 and 3 have zero/nan volume
    })
    cleaned = clean_daily_bars(df, "TEST.NS")
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["open"] == 100.0

def test_clean_daily_bars_drops_extreme_unadjusted_split_gap():
    # 50% overnight crash from 200 to 100 (classic unadjusted 2:1 stock split)
    df = pd.DataFrame({
        "open": [198.0, 100.0, 101.0],
        "high": [202.0, 103.0, 104.0],
        "low": [197.0, 99.0, 100.0],
        "close": [200.0, 100.0, 102.0], # 200 -> 100 is -50%
        "volume": [500000, 600000, 550000],
    })
    cleaned = clean_daily_bars(df, "SPLIT.NS")
    # Row 1 (the 50% split drop) should be purged
    assert len(cleaned) == 2
    assert 100.0 not in cleaned["close"].values

def test_clean_daily_bars_handles_empty_and_graceful():
    assert clean_daily_bars(None, "NONE.NS").empty
    assert clean_daily_bars(pd.DataFrame(), "EMPTY.NS").empty
