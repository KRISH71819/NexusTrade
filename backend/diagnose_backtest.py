"""
Phase 4.5 — backtester certification diagnostic.
Resolves the contradiction: always-long +19.3%/yr vs 97%-exposure
momentum -16.8%/yr vs dip-buying 37.7% win rate. These cannot coexist.
Splits blame: DATA vs SANDBOX CODE. Run: python diagnose_backtest.py
"""
import asyncio
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

from database import connect_db, close_db
from history_store import get_ohlcv

START = datetime(2010, 1, 1, tzinfo=timezone.utc)
TICKERS = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS"]


def ann(s: pd.Series) -> float:
    s = s.dropna()
    return float((1 + s).prod() ** (252 / len(s)) - 1) if len(s) else float("nan")


async def main() -> None:
    await connect_db()
    rets, poss = {}, {}
    print("=" * 78)
    print("CHECK A — data sanity + per-ticker momentum at 3 shifts (independent math)")
    print("=" * 78)
    for t in TICKERS:
        df = await get_ohlcv(t, start=START)
        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)
        ret = close.pct_change()
        sma20 = close.rolling(20).mean()

        bh_total = close.iloc[-1] / close.iloc[0] - 1
        drops8 = int((ret < -0.08).sum())
        print(f"\n{t}: rows={len(df)}  {df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()}")
        print(f"  buy&hold total (2010+): {bh_total:+.1%}   one-day drops >8%: {drops8}")

        for shift in (0, 1, 2):
            pos = (close > sma20).shift(shift).fillna(0).astype(float)
            print(f"  momentum annualized with pos shift({shift}): {ann(pos * ret):+.2%}")

        rets[t] = ret
        poss[t] = (close > sma20).shift(1).fillna(0).astype(float)

    print("\n" + "=" * 78)
    print("CHECK B — deployed momentum, independent reimplementation")
    print("=" * 78)
    ret_df = pd.DataFrame(rets).fillna(0.0)
    pos_df = pd.DataFrame(poss).fillna(0.0)
    act = pos_df.sum(axis=1)
    m = act > 0
    dg = pd.Series(0.0, index=ret_df.index)
    dg[m] = (ret_df * pos_df).sum(axis=1)[m] / act[m]
    print(f"  independent deployed momentum annualized: {ann(dg):+.2%}")
    print(f"  run_backtest reported momentum_20:        -16.81%")

    print("\n" + "=" * 78)
    print("CHECK C — visual alignment around first signal flip (RELIANCE)")
    print("=" * 78)
    df = await get_ohlcv(TICKERS[0], start=START)
    df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    sma20 = close.rolling(20).mean()
    pos = (close > sma20).shift(1).fillna(0).astype(float)
    ret = close.pct_change()
    flip = int(((pos.diff().abs() > 0) & (pos.index > 25)).idxmax())
    seg = pd.DataFrame({
        "date": df["date"].dt.date,
        "close": close.round(2),
        "sma20": sma20.round(2),
        "pos": pos.astype(int),
        "ret_pct": (ret * 100).round(2),
    }).iloc[max(0, flip - 3): flip + 4]
    print(seg.to_string(index=False))
    print("\nRule: on each row, pos must equal (close>sma20) of the PREVIOUS row,")
    print("and that pos earns THAT row's ret_pct.")
    await close_db()


asyncio.run(main())
