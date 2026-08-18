"""Pure-core tests for the Phase-8 meta research portfolio."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from meta_portfolio import build_rebalance_orders, should_rebalance, vol_target_scale


class TestOrderBuilder:
    def test_sells_exits_and_buys_targets_cash_constrained(self):
        holdings = [{"ticker": "OLD.NS", "quantity": 100}]
        prices = {"OLD.NS": 50.0, "A.NS": 100.0, "B.NS": 200.0}
        orders = build_rebalance_orders(
            holdings, ["A.NS", "B.NS"], prices,
            cash=10_000.0, total_value=20_000.0,
        )
        sells = [o for o in orders if o["side"] == "SELL"]
        buys = [o for o in orders if o["side"] == "BUY"]
        assert sells == [{"ticker": "OLD.NS", "side": "SELL", "quantity": 100, "price": 50.0}]
        assert all(b["quantity"] * b["price"] <= 10_000.0 for b in buys)
        assert len(buys) >= 1

    def test_no_targets_no_orders(self):
        assert build_rebalance_orders([], [], {}, 1000.0, 1000.0) == []

    def test_drift_band_skips_small_adjustments(self):
        holdings = [{"ticker": "A.NS", "quantity": 100}]   # 10k vs target 10k
        prices = {"A.NS": 100.0}
        orders = build_rebalance_orders(
            holdings, ["A.NS"], prices, cash=0.0, total_value=10_000.0)
        assert orders == []


class TestCadence:
    def test_first_run_true(self):
        assert should_rebalance(None, "2026-08-14", 20) is True

    def test_respects_cadence(self):
        assert should_rebalance("2026-08-01", "2026-08-14", 20) is False
        assert should_rebalance("2026-07-01", "2026-08-14", 20) is True


class TestVolTargetScale:
    def test_caps_at_max_in_low_vol(self):
        assert vol_target_scale(0.10, 0.18, 0.25, 1.0) == 1.0

    def test_scales_in_high_vol(self):
        assert vol_target_scale(0.36, 0.18, 0.25, 1.0) == pytest.approx(0.5)

    def test_floors_at_min(self):
        assert vol_target_scale(0.90, 0.18, 0.25, 1.0) == 0.25

    def test_zero_vol_returns_max(self):
        assert vol_target_scale(0.0, 0.18, 0.25, 1.0) == 1.0
