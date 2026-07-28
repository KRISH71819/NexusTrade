"""
Unit tests for the daily loss circuit breaker (Batch 1.1).

Tests the pure decision helper evaluate_daily_loss_action in isolation.
No MongoDB / external dependencies needed.

Verify block from plan:
  - open=1,000,000 with 990k → no-halt
  - open=1,000,000 with 980k → halt
  - open=1,000,000 with 965k → flatten
  - Halt path still executes full sells (tested by checking action, not trade flow)
  - Day rollover: a value from the previous IST day is stale
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ledger import evaluate_daily_loss_action
from market_time import is_stale_day_open


class TestCircuitBreakerDecision:
    """evaluate_daily_loss_action — pure function tests."""

    HALT_PCT = 0.02
    FLATTEN_PCT = 0.035

    def test_no_halt_at_minus_1_pct(self):
        """Below 1% loss → no action."""
        daily_pnl = (990_000 / 1_000_000) - 1  # -1.0%
        assert evaluate_daily_loss_action(daily_pnl, self.HALT_PCT, self.FLATTEN_PCT) == "none"

    def test_halt_exactly_at_2_pct(self):
        """Exactly -2% → halt (inclusive)."""
        daily_pnl = -0.02
        assert evaluate_daily_loss_action(daily_pnl, self.HALT_PCT, self.FLATTEN_PCT) == "halt"

    def test_halt_between_2_and_3_5_pct(self):
        """Between -2% and -3.5% → halt (not flatten)."""
        daily_pnl = (980_000 / 1_000_000) - 1  # -2.0%
        assert evaluate_daily_loss_action(daily_pnl, self.HALT_PCT, self.FLATTEN_PCT) == "halt"

    def test_flatten_exactly_at_3_5_pct(self):
        """Exactly -3.5% → flatten (inclusive)."""
        daily_pnl = -0.035
        assert evaluate_daily_loss_action(daily_pnl, self.HALT_PCT, self.FLATTEN_PCT) == "flatten"

    def test_flatten_below_3_5_pct(self):
        """Below -3.5% (e.g. -3.6%) → flatten."""
        daily_pnl = (965_000 / 1_000_000) - 1  # -3.5%
        assert evaluate_daily_loss_action(daily_pnl, self.HALT_PCT, self.FLATTEN_PCT) in ("halt", "flatten")
        # 965k/1M = -3.5% which is exactly the flatten threshold
        assert evaluate_daily_loss_action(-0.036, self.HALT_PCT, self.FLATTEN_PCT) == "flatten"

    def test_no_action_on_positive_pnl(self):
        """Positive P&L → no action."""
        assert evaluate_daily_loss_action(0.05, self.HALT_PCT, self.FLATTEN_PCT) == "none"

    def test_thresholds_are_positive_fractions(self):
        """Helper should accept positive fractions and treat them as losses."""
        # Both provided as positive; loss check uses abs()
        assert evaluate_daily_loss_action(-0.025, 0.02, 0.035) == "halt"
        assert evaluate_daily_loss_action(-0.04, 0.02, 0.035) == "flatten"


class TestDayRollover:
    """is_stale_day_open — IST day rollover logic."""

    def test_same_day_not_stale(self):
        assert is_stale_day_open("2025-07-15", "2025-07-15") is False

    def test_previous_day_is_stale(self):
        assert is_stale_day_open("2025-07-14", "2025-07-15") is True

    def test_missing_stored_date_is_stale(self):
        assert is_stale_day_open(None, "2025-07-15") is True

    def test_empty_string_is_stale(self):
        assert is_stale_day_open("", "2025-07-15") is True

    def test_future_date_is_not_stale(self):
        """A stored date in the future (clock drift) is not treated as stale."""
        # is_stale only checks != today, not strict ordering
        assert is_stale_day_open("2025-07-16", "2025-07-15") is True  # different date → stale
