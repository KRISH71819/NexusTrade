import pandas as pd
import pytest
from core.portfolio import (should_rebalance, vol_target_scale, select_names,
                            plan_rebalance_orders, apply_turnover_budget,
                            catastrophic_stops, swap_worth_it, composite_index,
                            trend_overlay_on)
from core.sleeves import sleeve_score, inverse_vol_weights, equal_weights, SLEEVE_SIGNALS

def test_cadence_and_vol_scale():
    assert should_rebalance(None, "2026-10-15", 60) is True
    assert should_rebalance("2026-09-01", "2026-10-15", 60) is False
    assert should_rebalance("2026-06-01", "2026-10-15", 60) is True
    assert vol_target_scale(0.10, 0.15, 0.25, 1.0) == 1.0
    assert vol_target_scale(0.30, 0.15, 0.25, 1.0) == pytest.approx(0.5)
    assert vol_target_scale(0.90, 0.15, 0.25, 1.0) == 0.25

def test_rank_buffer():
    ranked = [f"T{i}" for i in range(1, 41)]
    assert select_names(ranked, set(), 25, 35) == ranked[:25]
    assert "T30" in select_names(ranked, {"T30"}, 25, 35)      # held inside buffer stays
    assert "T36" not in select_names(ranked, {"T36"}, 25, 35)   # held past exit rank drops

def test_planner_band_and_exits():
    plan = plan_rebalance_orders([{"ticker": "A.NS", "quantity": 340}], ["A.NS"],
                                 {"A.NS": 100.0}, 100_000, 0.7, 2)
    assert plan["buys"] == [] and not plan["sells"]             # inside +/-10% band
    plan = plan_rebalance_orders([{"ticker": "OLD.NS", "quantity": 10}], ["A.NS"],
                                 {"OLD.NS": 10.0, "A.NS": 100.0}, 100_000, 0.7, 1)
    assert any(s["reason"] == "exit" for s in plan["sells"])

def test_turnover_budget_scales():
    orders = [{"ticker": "A", "quantity": 400, "side": "BUY"}]
    scaled, hit = apply_turnover_budget(orders, {"A": 100.0}, 100_000, 0.30)
    assert hit and scaled[0]["quantity"] <= 300
    small = [{"ticker": "A", "quantity": 100, "side": "BUY"}]
    kept, hit2 = apply_turnover_budget(small, {"A": 100.0}, 100_000, 0.30)
    assert not hit2 and kept == small

def test_catastrophic_stop_and_swap_skip():
    h = [{"ticker": "X", "avg_price": 100.0}]
    assert catastrophic_stops(h, {"X": 74.0}) == ["X"]
    assert catastrophic_stops(h, {"X": 76.0}) == []
    assert swap_worth_it(30, 24) is True
    assert swap_worth_it(30, 26) is False

def test_weights():
    w = inverse_vol_weights({"a": 0.10, "b": 0.30})
    assert w["a"] == pytest.approx(0.75) and abs(sum(w.values()) - 1.0) < 1e-9
    assert equal_weights(["a", "b"]) == {"a": 0.5, "b": 0.5}

def test_sleeve_m_on_rising_panel():
    dates = pd.date_range("2023-01-01", periods=300, freq="B")
    panel = {}
    for i, drift in enumerate([0.10, 0.05, -0.05]):
        close = 100 + drift * np.arange(300) if False else [100 + drift * t for t in range(300)]
        panel[f"T{i}.NS"] = pd.DataFrame({"date": dates, "open": close, "high": close,
                                          "low": close, "close": close,
                                          "volume": [1_000_000] * 300})
    scores = sleeve_score(panel, "M")
    assert scores["T0.NS"] > scores["T2.NS"]
    assert all(SLEEVE_SIGNALS[k] for k in ("M", "D", "R", "V"))

def test_trend_overlay():
    up = pd.Series([100 + i for i in range(250)])
    assert trend_overlay_on(up, 200) is True
    down = pd.Series([200 - i for i in range(250)])
    assert trend_overlay_on(down, 200) is False
    assert composite_index(pd.DataFrame({"a": up, "b": up})).iloc[-1] == pytest.approx(3.49)
