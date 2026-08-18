"""Ranking engine sanity: it must buy the strongest ticker."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pandas as pd
from alpha_sandbox.sandbox import backtest_ranking

def _panel():
    n = 300
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    t = np.arange(n)
    frames = {}
    for tick, drift in [("WIN.NS", 0.1), ("FLAT.NS", 0.0), ("LOSE.NS", -0.1)]:
        close = 100 + drift * t
        frames[tick] = pd.DataFrame({
            "date": dates, "open": close, "high": close,
            "low": close, "close": close, "volume": 1_000_000,
        })
    return frames

def test_ranking_picks_winner():
    daily, info = backtest_ranking(
        _panel(), "close / sma(close, 20) - 1", top_n=1, rebalance_days=5
    )
    assert info["exposure_pct"] > 75
    assert daily.mean() > 0.0005   # holding the uptrending ticker, net of costs


from alpha_sandbox.sandbox import backtest_ranking_from_signal


class TestRankingFromSignal:
    def test_top_n_clamps_and_stays_invested(self):
        import numpy as np
        dates = pd.date_range("2020-01-01", periods=200, freq="B")
        t = np.arange(200)
        close = pd.DataFrame(
            {"A": 100 + 0.10 * t, "B": 100 + 0.05 * t, "C": 100 + 0.01 * t},
            index=dates,
        )
        sig = close.pct_change(20).fillna(0.0)
        daily, info = backtest_ranking_from_signal(sig, close, top_n=10, rebalance_days=20)
        assert info["tickers_used"] == 3          # top_n clamped to available
        assert info["exposure_pct"] > 65          # fully invested after warmup
        assert daily.mean() > 0                   # holds the trending names
