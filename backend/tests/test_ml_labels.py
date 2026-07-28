"""
Unit tests for Batch 3.1 — ML label fix.

Verify from plan:
  - On a strictly rising series, assert no label is 0 (fake DOWN).
  - NaN tail rows are properly propagated (not silently set to 0).
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np


# Import _create_targets from ml_engine
from ml_engine import _create_targets


class TestMLLabelFix:
    """Batch 3.1 — NaN-safe target creation."""

    def _make_strictly_rising_df(self, n: int = 50) -> pd.DataFrame:
        """Create a strictly rising close price series with required columns."""
        prices = [100.0 + i * 0.5 for i in range(n)]
        return pd.DataFrame({
            "close": prices,
            "open": prices,
            "high": [p + 0.2 for p in prices],
            "low": [p - 0.2 for p in prices],
            "volume": [1_000_000] * n,
        })

    def test_no_fake_zero_labels_on_rising_series(self):
        """
        On a strictly rising series every clean label must be 1 (UP).
        The NaN-filled tail rows (last 5) will produce NaN → dropna removes them.
        No label should be 0 (which would be a fake DOWN from the old bug).
        """
        df = self._make_strictly_rising_df(50)
        targets = _create_targets(df)

        # Drop rows where any target is NaN (tail rows without future bars)
        clean = targets.dropna()

        # On a strictly rising series all clean labels must be 1
        assert len(clean) > 0, "No clean labels — series too short"
        for col in ["target_1", "target_3", "target_5", "target_combined"]:
            zeros = (clean[col] == 0).sum()
            assert zeros == 0, (
                f"Column {col} has {zeros} fake-zero labels on a strictly rising series"
            )

    def test_tail_rows_are_nan_not_zero(self):
        """
        The last 5 rows should produce NaN (not 0) for target_5, because the
        future bar at horizon 5 does not exist.
        The old bug returned 0 (NaN > x = False → 0).
        """
        df = self._make_strictly_rising_df(20)
        targets = _create_targets(df)

        # Last 5 rows of target_5 should be NaN
        tail_5 = targets["target_5"].iloc[-5:]
        nan_count = tail_5.isna().sum()
        assert nan_count == 5, (
            f"Expected 5 NaN rows at tail of target_5, got {nan_count} "
            f"(values: {tail_5.tolist()})"
        )

    def test_target_3_tail_has_3_nans(self):
        """Last 3 rows of target_3 should be NaN."""
        df = self._make_strictly_rising_df(20)
        targets = _create_targets(df)
        tail_3 = targets["target_3"].iloc[-3:]
        nan_count = tail_3.isna().sum()
        assert nan_count == 3, f"Expected 3 NaN rows at tail of target_3, got {nan_count}"

    def test_target_1_tail_has_1_nan(self):
        """Last 1 row of target_1 should be NaN."""
        df = self._make_strictly_rising_df(20)
        targets = _create_targets(df)
        last_val = targets["target_1"].iloc[-1]
        assert pd.isna(last_val), f"Last target_1 should be NaN, got {last_val}"

    def test_combined_target_nan_when_any_horizon_is_nan(self):
        """target_combined must be NaN wherever any of the three horizons are NaN."""
        df = self._make_strictly_rising_df(20)
        targets = _create_targets(df)

        # Where target_5 is NaN, target_combined must also be NaN
        for idx in targets.index[-5:]:
            assert pd.isna(targets.loc[idx, "target_combined"]), (
                f"Row {idx}: target_combined should be NaN when target_5 is NaN"
            )
