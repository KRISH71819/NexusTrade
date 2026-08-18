import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from meta_strategy import plan_rebalance_orders


def test_planner_sizes_to_target():
    plan = plan_rebalance_orders([], ["A.NS", "B.NS"], {"A.NS": 100.0, "B.NS": 50.0},
                                 100_000, 0.7, 2)
    assert plan["buys"][0]["quantity"] == 350   # 35k / 100
    assert plan["buys"][1]["quantity"] == 700   # 35k / 50


def test_planner_exits_and_trims():
    holdings = [{"ticker": "OLD.NS", "quantity": 10}, {"ticker": "A.NS", "quantity": 1000}]
    plan = plan_rebalance_orders(holdings, ["A.NS"], {"OLD.NS": 10.0, "A.NS": 100.0},
                                 100_000, 0.7, 1)
    assert any(s["ticker"] == "OLD.NS" and s["reason"] == "exit" for s in plan["sells"])
    # A: 100k vs target 70k (+10% band=77k) → trim (100k-70k)//100 = 300
    assert any(s["ticker"] == "A.NS" and s["quantity"] == 300 for s in plan["sells"])


def test_planner_skips_no_price():
    plan = plan_rebalance_orders([], ["X.NS"], {}, 100_000, 0.7, 1)
    assert plan["skipped"] == [{"ticker": "X.NS", "reason": "no_price"}]


def test_planner_respects_band():
    holdings = [{"ticker": "A.NS", "quantity": 340}]   # 34k vs 35k target → inside band
    plan = plan_rebalance_orders(holdings, ["A.NS"], {"A.NS": 100.0}, 100_000, 0.7, 2)
    assert plan["buys"] == [] and not any(s["ticker"] == "A.NS" for s in plan["sells"])
