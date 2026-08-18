"""
Signal Library — canonical cross-sectional base signals for the meta-scorer.
All signals are causal DSL expressions; higher value = more attractive long.
"""
import logging

import pandas as pd

from alpha_sandbox.dsl import evaluate_expression

logger = logging.getLogger(__name__)

SIGNALS = {
    "mom_60": "close / sma(close, 60) - 1",
    "mom_120": "close / sma(close, 120) - 1",
    "rev_5": "-zscore(close, 5)",
    "rev_21": "-(close / sma(close, 21) - 1)",
    "volmom_60": "(close / sma(close, 60) - 1) * volume_ratio(volume, 20)",
    "trend_200": "close / sma(close, 200) - 1",
}


def close_panel(panel: dict) -> pd.DataFrame:
    """{ticker: df} -> DataFrame(index=date, columns=tickers) of closes."""
    return pd.DataFrame(
        {t: df.set_index("date")["close"].astype(float) for t, df in panel.items()}
    )


def build_signal_panels(panel: dict, names=None) -> dict:
    """Returns {signal_name: DataFrame(index=date, columns=tickers)}."""
    names = names or list(SIGNALS)
    frames = {name: {} for name in names}
    for ticker, df in panel.items():
        indexed = df.set_index("date").sort_index()
        for name in names:
            try:
                frames[name][ticker] = evaluate_expression(SIGNALS[name], indexed)
            except Exception as e:
                logger.warning(f"{ticker}/{name}: {e}")
    return {name: pd.DataFrame(cols) for name, cols in frames.items()}
