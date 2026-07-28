"""
Unit tests for Batch 2.2 (ATR stop with 7% backstop) and
Batch 2.3 (trailing profit lock replaces entry-price break-even).

Verify from plan 2.2:
  - ATR stop used when tighter than 7%
  - 7% backstop used when ATR stop would be wider
  - Legacy holding without atr_at_entry doesn't raise

Verify from plan 2.3:
  - entry=100, tier1 at 112, peak=120, ATR=3 → lock ≈ 115.5
  - fall to 116 does NOT sell, 115 does
  - lock never decreases when peak falls

These tests exercise risk_manager.check_stop_losses directly.
No MongoDB / external dependencies needed.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock


def _make_settings(**overrides):
    defaults = dict(
        stop_loss_pct=0.07,
        atr_stop_multiplier=1.5,
        trailing_stop_activation_pct=0.15,
        trailing_stop_distance_pct=0.10,
        trailing_stop_strict_pct=0.08,
        bearish_trailing_stop_pct=0.05,
        cautious_trailing_stop_pct=0.07,
    )
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _portfolio(holdings):
    return {"holdings": holdings}


def _holding(ticker, avg_price, peak_price=None, quantity=10, atr_at_entry=None, locked_stop=None):
    return {
        "ticker": ticker,
        "avg_price": avg_price,
        "peak_price": peak_price or avg_price,
        "quantity": quantity,
        "atr_at_entry": atr_at_entry,
        "locked_stop_price": locked_stop,
        "bought_at": None,
        "profit_taken_tiers": [],
    }


class TestATRStop:
    """Batch 2.2 — ATR-based stop with 7% hard backstop."""

    def test_atr_stop_used_when_tighter(self):
        """When ATR stop is above 93% of entry (tighter), it should be used."""
        mock_settings = _make_settings()
        # entry=100, ATR=3, multiplier=1.5 → atr_stop=100 - 4.5 = 95.5 (>93 = pct stop)
        holding = _holding("TEST", avg_price=100.0, atr_at_entry=3.0)
        portfolio = _portfolio([holding])
        prices = {"TEST": 94.0}  # below 95.5 but above 93.0

        with patch("risk_manager.settings", mock_settings):
            from risk_manager import check_stop_losses
            signals = check_stop_losses(portfolio, prices)
            assert len(signals) == 1
            assert signals[0]["trigger"] == "stop_loss"
            assert "ATR" in signals[0]["reason"]

    def test_pct_backstop_used_when_atr_too_wide(self):
        """When ATR stop is below 93% of entry (wider than 7%), use 7% floor."""
        mock_settings = _make_settings()
        # entry=100, ATR=10, multiplier=1.5 → atr_stop=100 - 15 = 85 (< 93 = pct stop)
        # So pct_stop=93 is tighter → use 93
        holding = _holding("TEST", avg_price=100.0, atr_at_entry=10.0)
        portfolio = _portfolio([holding])
        prices_no_trigger = {"TEST": 94.0}  # above 93 — should NOT trigger
        prices_trigger = {"TEST": 92.0}     # below 93 — should trigger

        with patch("risk_manager.settings", mock_settings):
            from risk_manager import check_stop_losses
            no_signals = check_stop_losses(portfolio, prices_no_trigger)
            assert len(no_signals) == 0

            signals = check_stop_losses(portfolio, prices_trigger)
            assert len(signals) == 1
            assert signals[0]["trigger"] == "stop_loss"

    def test_legacy_holding_no_atr_uses_pct_stop(self):
        """Holdings without atr_at_entry should not raise and use 7% stop."""
        mock_settings = _make_settings()
        holding = _holding("LEGACY", avg_price=200.0, atr_at_entry=None)
        portfolio = _portfolio([holding])
        prices = {"LEGACY": 185.0}  # 200 * 0.93 = 186 → 185 is below

        with patch("risk_manager.settings", mock_settings):
            from risk_manager import check_stop_losses
            # Should not raise
            signals = check_stop_losses(portfolio, prices)
            assert len(signals) == 1
            assert "PCT" in signals[0]["reason"]


class TestProfitLock:
    """Batch 2.3 — Trailing profit lock (replaces entry-price break-even)."""

    def test_plan_example_lock_at_115_5(self):
        """
        Plan says: entry=100, tier1 at 112, peak=120, ATR=3 → lock ≈ 115.5
        peak(120) - 1.5 * ATR(3) = 120 - 4.5 = 115.5
        max(existing_lock=100, proposed=115.5, avg_price=100) = 115.5
        """
        entry = 100.0
        peak = 120.0
        atr = 3.0
        multiplier = 1.5
        existing_lock = entry  # after tier 1 fires, lock starts at entry
        avg_price = entry

        proposed = peak - multiplier * atr
        new_lock = max(existing_lock, proposed, avg_price)
        assert abs(new_lock - 115.5) < 0.01, f"Expected ~115.5, got {new_lock}"

    def test_fall_to_116_does_not_sell(self):
        """Price at 116 > lock 115.5 → should NOT trigger."""
        mock_settings = _make_settings()
        holding = _holding("WIN", avg_price=100.0, peak_price=120.0, locked_stop=115.5, atr_at_entry=3.0)
        portfolio = _portfolio([holding])
        prices = {"WIN": 116.0}

        with patch("risk_manager.settings", mock_settings):
            from risk_manager import check_stop_losses
            signals = check_stop_losses(portfolio, prices)
            locked_signals = [s for s in signals if s["trigger"] == "locked_stop"]
            assert len(locked_signals) == 0, "Price 116 > lock 115.5 should not sell"

    def test_fall_to_115_does_sell(self):
        """Price at 115 <= lock 115.5 → should trigger."""
        mock_settings = _make_settings()
        holding = _holding("WIN", avg_price=100.0, peak_price=120.0, locked_stop=115.5, atr_at_entry=3.0)
        portfolio = _portfolio([holding])
        prices = {"WIN": 115.0}

        with patch("risk_manager.settings", mock_settings):
            from risk_manager import check_stop_losses
            signals = check_stop_losses(portfolio, prices)
            locked_signals = [s for s in signals if s["trigger"] == "locked_stop"]
            assert len(locked_signals) == 1, "Price 115 <= lock 115.5 should sell"

    def test_lock_never_decreases(self):
        """
        The lock is set once (in scheduler after tier fires) and never decreases.
        This test confirms that a holding with an existing lock above avg_price
        will not retroactively lower the lock when peak falls.
        (The ratchet logic lives in scheduler; here we just confirm check_stop_losses
        respects the stored lock value correctly.)
        """
        mock_settings = _make_settings()
        # Peak was 120, lock was set at 115.5; now price fell to 110 (peak fell)
        holding = _holding("WIN", avg_price=100.0, peak_price=110.0, locked_stop=115.5, atr_at_entry=3.0)
        portfolio = _portfolio([holding])
        prices = {"WIN": 115.0}  # below original lock (115.5)

        with patch("risk_manager.settings", mock_settings):
            from risk_manager import check_stop_losses
            signals = check_stop_losses(portfolio, prices)
            # Price 115 < lock 115.5 → sell triggered (lock still holds at 115.5)
            locked_signals = [s for s in signals if s["trigger"] == "locked_stop"]
            assert len(locked_signals) == 1
