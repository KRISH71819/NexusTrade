import pandas as pd
from core.costs import calculate_trade_charges, apply_slippage, one_side_cost_rate
from core.dsl import validate_expression, evaluate_expression

def _df(n=120):
    prices = [100 + i * 0.5 for i in range(n)]
    return pd.DataFrame({"open": prices, "high": [p + 1 for p in prices],
                         "low": [p - 1 for p in prices], "close": prices,
                         "volume": [1_000_000] * n})

class TestCosts:
    def test_round_trip_70k(self):
        buy = calculate_trade_charges(70_000, "BUY")
        sell = calculate_trade_charges(70_000, "SELL")
        total = buy["total_charges"] + sell["total_charges"]
        assert 220.24 <= total <= 222.24
    def test_dp_zero_on_buy_once_on_sell(self):
        assert calculate_trade_charges(50_000, "BUY")["dp_charge"] == 0.0
        assert calculate_trade_charges(1_000, "SELL")["dp_charge"] == 15.0
        assert calculate_trade_charges(1_000_000, "SELL")["dp_charge"] == 15.0
    def test_gst_includes_dp(self):
        r = calculate_trade_charges(100_000, "SELL")
        expected = round((20.0 + 100_000 * 0.0000345 + 15.0) * 0.18, 2)
        assert abs(r["gst"] - expected) < 0.01
    def test_stamp_only_buy(self):
        assert calculate_trade_charges(100_000, "BUY")["stamp_duty"] > 0
        assert calculate_trade_charges(100_000, "SELL")["stamp_duty"] == 0.0
    def test_slippage_direction(self):
        assert apply_slippage(100.0, "BUY") == 100.40
        assert apply_slippage(100.0, "SELL") == 99.60
    def test_one_side_rate_positive(self):
        assert one_side_cost_rate() > 0

class TestDSL:
    def test_rejects_dangerous(self):
        for bad in ["__import__('os').system('ls')", "close.mean", "close[0]", "close + secret"]:
            ok, _ = validate_expression(bad)
            assert not ok
    def test_accepts_classic(self):
        ok, err = validate_expression("close / sma(close, 20) - 1")
        assert ok, err
    def test_momentum_sign_and_warmup(self):
        s = evaluate_expression("close / sma(close, 20) - 1", _df())
        assert s.iloc[-1] > 0
        assert s.iloc[:19].isna().all()
