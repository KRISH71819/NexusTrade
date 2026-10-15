"""
Unit Tests for Phase 4: EOD Paper Book, State Machine, MTM, Regime Engine, Rebalance Planner.
Strictly isolated, zero network I/O, runs in < 2 seconds.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.book import Book
from core.regime import classify_regime, PHASE4_CAPS
from core.planner import (
    TradeIntent,
    check_catastrophic_stops,
    swap_worth_it,
    should_rebalance,
    apply_turnover_budget_scale_down,
    plan_rebalance,
    v2_daily_mtm_and_regime,
    v2_rebalance_planner,
)


class MockCollection:
    """Async MongoDB collection mock supporting find_one, update_one, insert_one."""
    def __init__(self, data=None):
        self.docs = data if data is not None else {}

    async def find_one(self, filter_dict):
        key = filter_dict.get("_id") or filter_dict.get("date")
        return self.docs.get(key)

    async def update_one(self, filter_dict, update_dict, upsert=False):
        key = filter_dict.get("_id") or filter_dict.get("date")
        set_vals = update_dict.get("$set", {})
        if key not in self.docs:
            if upsert:
                self.docs[key] = {**filter_dict, **set_vals}
        else:
            self.docs[key].update(set_vals)
        return MagicMock(acknowledged=True)

    async def insert_one(self, doc):
        key = doc.get("_id") or doc.get("date") or len(self.docs)
        self.docs[key] = doc
        return MagicMock(acknowledged=True)


class MockDB:
    """Mock Database dictionary container."""
    def __init__(self):
        self.collections = {
            "book": MockCollection(),
            "book_trades": MockCollection(),
            "book_equity": MockCollection(),
            "regime_log": MockCollection(),
            "system_state_v2": MockCollection(),
        }

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = MockCollection()
        return self.collections[name]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TestBookEngine
# ═══════════════════════════════════════════════════════════════════════════════

class TestBookEngine:
    def test_avg_cost_basis_scaling(self):
        """Verify weighted average cost on partial buys/sells."""
        book = Book(cash=100_000.0)

        # 1. Buy 100 @ 100.0 -> avg = 100.0
        book.apply_trade("T1", "BUY", 100, 100.0)
        assert book.holdings["T1"]["quantity"] == 100
        assert book.holdings["T1"]["avg_price"] == 100.0

        # 2. Buy 100 @ 120.0 -> new avg = (100*100 + 100*120)/200 = 110.0
        book.apply_trade("T1", "BUY", 100, 120.0)
        assert book.holdings["T1"]["quantity"] == 200
        assert book.holdings["T1"]["avg_price"] == 110.0

        # 3. Partial sell 50 @ 130.0 -> avg cost basis remains 110.0
        book.apply_trade("T1", "SELL", 50, 130.0)
        assert book.holdings["T1"]["quantity"] == 150
        assert book.holdings["T1"]["avg_price"] == 110.0

    def test_realized_pnl_calculation(self):
        """Ensure realized P&L is accurately recorded on sells taking charges into account."""
        book = Book(cash=100_000.0)
        book.apply_trade("T1", "BUY", 100, 100.0)

        # Sell 50 shares @ 120.0 (gross gain: 50 * 20 = 1000.0)
        rec = book.apply_trade("T1", "SELL", 50, 120.0)
        charges = rec["charges"]["total_charges"]
        expected_realized = round(1000.0 - charges, 2)

        assert rec["realized_pnl"] == expected_realized
        assert book.realized_pnl == expected_realized

    def test_cash_reserve_guard(self):
        """Verify book rejects buys that exceed cash, and planner cannot generate buys if cash < 0."""
        # 1. Book level
        book = Book(cash=5_000.0)
        with pytest.raises(ValueError, match="Insufficient cash"):
            book.apply_trade("T1", "BUY", 100, 100.0)  # Requires ~10,000 cash

        # 2. Planner level: cash <= 0
        broke_book = Book(cash=0.0)
        intents = plan_rebalance(
            book=broke_book,
            ranked_universe=["T1", "T2"],
            prices={"T1": 100.0, "T2": 100.0},
        )
        assert [i for i in intents if i.side == "BUY"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TestMTMAndCatastrophicStops
# ═══════════════════════════════════════════════════════════════════════════════

class TestMTMAndCatastrophicStops:
    def test_catastrophic_stop_trigger(self):
        """Mock a holding dropping 26% from avg cost. Assert a SELL intent is generated."""
        holdings = [{"ticker": "CRASH", "quantity": 100, "avg_price": 100.0}]
        # 26% drop: price = 74.0 <= 75.0 (avg * 0.75)
        stops = check_catastrophic_stops(holdings, {"CRASH": 74.0}, stop_pct=0.25)
        assert len(stops) == 1
        assert stops[0].ticker == "CRASH"
        assert stops[0].side == "SELL"
        assert stops[0].quantity == 100
        assert stops[0].price == 74.0
        assert stops[0].reason == "catastrophic_stop_25pct"

        # 24% drop: price = 76.0 > 75.0 -> Not triggered
        stops_safe = check_catastrophic_stops(holdings, {"CRASH": 76.0}, stop_pct=0.25)
        assert len(stops_safe) == 0

    def test_catastrophic_stop_ignores_buffer(self):
        """Assert the 25% stop overrides the Rank > 35 exit buffer (even at Rank 1)."""
        # Holding is Rank 1 in the qualified universe
        ranked = ["A", "B", "C"]
        book = Book(
            cash=100_000.0,
            holdings={"A": {"ticker": "A", "quantity": 100, "avg_price": 100.0, "current_price": 74.0}},
        )
        prices = {"A": 74.0, "B": 100.0, "C": 100.0}

        intents = plan_rebalance(book, ranked, prices, top_n=25, exit_n=35)
        # Even though "A" is Rank 1, catastrophic stop flags it for immediate exit
        a_sells = [i for i in intents if i.ticker == "A" and i.side == "SELL"]
        assert len(a_sells) == 1
        assert a_sells[0].reason == "catastrophic_stop_25pct"

    @pytest.mark.asyncio
    async def test_equity_snapshot_persistence(self):
        """Verify book_equity receives exactly one document per MTM run."""
        db = MockDB()
        book = Book(cash=100_000.0, holdings={"T1": {"ticker": "T1", "quantity": 100, "avg_price": 100.0, "current_price": 105.0}})
        await book.save(db)

        res = await v2_daily_mtm_and_regime(
            db=db,
            date_str="2026-10-15",
            prices={"T1": 110.0},
            breadth_pct=65.0,
        )
        assert res["status"] == "ok"
        equity_docs = db["book_equity"].docs
        assert len(equity_docs) == 1
        assert "2026-10-15" in equity_docs
        doc = equity_docs["2026-10-15"]
        assert doc["total_equity"] == round(100_000.0 + 100 * 110.0, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TestRegimeEngine
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegimeEngine:
    def test_crisis_regime_liquidation(self):
        """Mock Breadth = 15%. Assert Regime = CRISIS and Target Exposure = 0%."""
        res = classify_regime(breadth_pct=15.0, index_gap_pct=0.0)
        assert res["regime"] == "CRISIS"
        assert res["target_exposure"] == 0.0

        # When planner runs in CRISIS, all positions are liquidated
        book = Book(
            cash=50_000.0,
            holdings={"A": {"ticker": "A", "quantity": 100, "avg_price": 100.0, "current_price": 100.0}},
        )
        intents = plan_rebalance(book, ["A", "B"], {"A": 100.0, "B": 100.0}, regime="CRISIS")
        assert len(intents) == 1
        assert intents[0].ticker == "A"
        assert intents[0].side == "SELL"
        assert intents[0].reason == "crisis_liquidation"

    def test_neutral_regime_exposure(self):
        """Mock Breadth = 55%. Assert Regime = NEUTRAL and Target Exposure = 80%."""
        res = classify_regime(breadth_pct=55.0, index_gap_pct=0.0)
        assert res["regime"] == "NEUTRAL"
        assert res["target_exposure"] == 0.80

    def test_additional_regime_matrix(self):
        """Verify remaining regime matrix bounds: RISK_OFF (50%) and RISK_ON (100%)."""
        # Breadth < 40% -> RISK_OFF
        assert classify_regime(35.0)["regime"] == "RISK_OFF"
        assert classify_regime(35.0)["target_exposure"] == 0.50

        # Breadth > 70% and Index Gap > 0% -> RISK_ON
        assert classify_regime(75.0, index_gap_pct=2.0)["regime"] == "RISK_ON"
        assert classify_regime(75.0, index_gap_pct=2.0)["target_exposure"] == 1.00


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TestRebalancePlanner
# ═══════════════════════════════════════════════════════════════════════════════

class TestRebalancePlanner:
    def test_60_day_cadence_gate(self):
        """Assert planner returns [] intents if last rebalance was 40 days ago."""
        book = Book(cash=100_000.0)
        # 40 days elapsed (Sep 5 to Oct 15) < 60 days
        intents = plan_rebalance(
            book=book,
            ranked_universe=["A", "B"],
            prices={"A": 100.0, "B": 100.0},
            last_rebalance_date="2026-09-05",
            today_str="2026-10-15",
            regime="NEUTRAL",
            prev_regime="NEUTRAL",
        )
        assert intents == []

    def test_rank_buffers_prevent_churn(self):
        """Mock a holding at Rank 28. Assert it is NOT sold (Exit buffer is > 35)."""
        ranked = [f"T{i}" for i in range(1, 28)] + ["H"] + [f"T{i}" for i in range(29, 36)] + ["OLD"] + [f"T{i}" for i in range(37, 50)]
        prices = {t: 100.0 for t in ranked}

        # Holding "H" is at Rank 28 (<= 35, within exit buffer), holding "OLD" is at Rank 36 (> 35)
        # Sized at target (~35 shares @ 100) so H is inside no-trade band
        book = Book(
            cash=100_000.0,
            holdings={
                "H": {"ticker": "H", "quantity": 35, "avg_price": 100.0, "current_price": 100.0},
                "OLD": {"ticker": "OLD", "quantity": 35, "avg_price": 100.0, "current_price": 100.0},
            },
        )
        intents = plan_rebalance(book, ranked, prices, top_n=25, exit_n=35)
        sell_tickers = [i.ticker for i in intents if i.side == "SELL"]
        # H (Rank 28) is retained inside buffer -> NOT sold
        assert "H" not in sell_tickers
        # OLD (Rank 36) exceeds exit buffer 35 -> forced exit
        assert "OLD" in sell_tickers
        assert any(i.ticker == "OLD" and "exit" in i.reason for i in intents)


    def test_no_trade_band_ignores_drift(self):
        """Mock a holding at 9.5% weight vs 10% target. Assert no trade is generated (< 10% band)."""
        # Portfolio total value = 100,000. Target exposure = 1.0, 10 stocks -> target per stock = 10,000 (10%).
        # Holding H has 95 shares @ 100 = 9,500 (9.5%).
        # Difference = |9.5% - 10.0%| = 0.5% <= 1.0% (10% of 10%). Inside band -> NO trade.
        book = Book(
            cash=90_500.0,
            holdings={"H": {"ticker": "H", "quantity": 95, "avg_price": 100.0, "current_price": 100.0}},
        )
        ranked = ["H"] + [f"T{i}" for i in range(2, 11)]
        prices = {t: 100.0 for t in ranked}

        intents = plan_rebalance(book, ranked, prices, target_exposure=1.0, top_n=10)
        h_trades = [i for i in intents if i.ticker == "H"]
        assert h_trades == []

    def test_cost_aware_swap_skip(self):
        """Mock Stock A (Rank 10) and Stock B (Rank 13). Assert swap is skipped (Delta Rank < 5)."""
        assert swap_worth_it(10, 13, min_delta=5) is False
        assert swap_worth_it(10, 16, min_delta=5) is True

        # In planner: Portfolio is full with T1..T9 and A (rank 13). B (rank 10) is entry candidate.
        # Delta = |13 - 10| = 3 < 5 -> Swap of A for B is skipped.
        ranked = [f"T{i}" for i in range(1, 10)] + ["B", "T11", "T12", "A"]
        prices = {t: 100.0 for t in ranked}

        holdings = {
            f"T{i}": {"ticker": f"T{i}", "quantity": 50, "avg_price": 100.0, "current_price": 100.0}
            for i in range(1, 10)
        }
        holdings["A"] = {"ticker": "A", "quantity": 50, "avg_price": 100.0, "current_price": 100.0}

        book = Book(cash=50_000.0, holdings=holdings)
        # top_n=10, exit_n=12: A (rank 13) would normally be exited for B (rank 10)
        intents = plan_rebalance(book, ranked, prices, top_n=10, exit_n=12, min_swap_rank_delta=5)

        # Because Delta Rank = 3 < 5, A is NOT exited and B is NOT bought
        assert not any(i.ticker == "A" and i.side == "SELL" and "exit" in i.reason for i in intents)
        assert not any(i.ticker == "B" and i.side == "BUY" for i in intents)


    def test_turnover_budget_scale_down(self):
        """Mock a regime shift requiring 50% turnover. Assert the planner scales non-mandatory adjustments down by 30%."""
        total_equity = 100_000.0
        # Create non-mandatory drift adjustment requiring 50,000 turnover (50%)
        drift_intent = TradeIntent(
            ticker="T1",
            side="BUY",
            quantity=500,
            price=100.0,  # 50,000 turnover = 50%
            reason="drift_rebalance_buy",
        )
        scaled, hit = apply_turnover_budget_scale_down(
            [drift_intent],
            total_equity=total_equity,
            turnover_limit=0.30,
            scale_down_pct=0.30,
        )
        assert hit is True
        # Scaled down by 30%: 500 * (1 - 0.30) = 350
        assert scaled[0].quantity == 350
        assert "scaled_30pct" in scaled[0].reason


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TestSchedulerHooks
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulerHooks:
    @pytest.mark.asyncio
    async def test_kill_switch_blocks_rebalance(self):
        """Mock system_state_v2 kill switch = True. Assert MTM runs, but Rebalance Planner aborts."""
        db = MockDB()
        # Set kill switch to True
        db["system_state_v2"].docs["v2_state"] = {"_id": "v2_state", "kill_switch_enabled": True}

        # 1. Rebalance Planner MUST abort
        reb_res = await v2_rebalance_planner(db=db, date_str="2026-10-15")
        assert reb_res["status"] == "aborted"
        assert reb_res["reason"] == "kill_switch_active"
        assert reb_res["trades"] == []

        # 2. MTM MUST still run (monitoring & equity preservation)
        mtm_res = await v2_daily_mtm_and_regime(db=db, date_str="2026-10-15")
        assert mtm_res["status"] == "ok"
        assert "2026-10-15" in db["book_equity"].docs
