"""
Centralized configuration — loads from .env with sensible defaults.
"""

import json
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List

from nifty_stocks import NIFTY_STOCKS

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # ── MongoDB ──────────────────────────────────────────────────────────
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "paper_trader"

    # ── Google Gemini ────────────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemma-4-31b-it"
    gemini_fallback_model: str = "gemini-3.1-flash-lite"

    # ── NewsData.io ──────────────────────────────────────────────────────
    newsdata_api_key: str = ""

    # ── Finnhub (fallback news) ──────────────────────────────────────────
    finnhub_api_key: str = ""

    # ── Telegram ─────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    dashboard_url: str = "http://localhost:3000"

    # ── Agent Config ─────────────────────────────────────────────────────
    initial_balance: float = 1_000_000.0
    max_position_pct: float = 0.20  # max 20% of portfolio per ticker
    watchlist: List[str] | str = NIFTY_STOCKS
    max_candidates_for_ai: int = 40
    run_analysis_on_startup: bool = True

    # ── Risk Management ──────────────────────────────────────────────────
    stop_loss_pct: float = 0.07          # sell if position drops 7% — midcaps need breathing room (was 5%: normal volatility triggered exits)
    trailing_stop_activation_pct: float = 0.15  # activate trailing after 15% gain
    trailing_stop_distance_pct: float = 0.10    # trailing stop at 10% from peak (after activation)
    trailing_stop_strict_pct: float = 0.08       # ALWAYS-ON: sell if price drops 8% from peak — give winners room to oscillate (was 6%: killed positions after Tier-1 profit take)
    max_drawdown_pct: float = 0.15       # halt buying if portfolio down 15%
    max_sector_stocks: int = 3           # max holdings per sector

    # ── Underperformer Detection ─────────────────────────────────────────
    underperformer_days: int = 15              # check performance over 15 days — midcaps consolidate 10-14 days routinely (was 10)
    underperformer_min_loss_pct: float = 0.05  # flag if losing > 5% over N days
    underperformer_stagnant_pct: float = 0.03  # flag if moved < 3% in 15 days — 2% was too tight, killed consolidating stocks (was 0.02)
    profit_take_partial_pct: float = 0.25      # sell 25% of position when taking profit
    profit_take_threshold_pct: float = 0.20    # take partial profit at 20%+ gain

    # ── Portfolio Rotation ────────────────────────────────────────────────
    rotation_min_score_gap: float = 0.25       # min score advantage for a swap
    rotation_min_hold_hours: float = 120.0     # must hold at least 5 days before rotation

    # ── Market Regime Filter ────────────────────────────────────────────
    market_regime_index: str = "^NSEI"            # NIFTY 50 index symbol
    market_regime_sma_period: int = 50            # 50-day SMA for bull/bear regime
    bearish_trailing_stop_pct: float = 0.05       # 5% trailing in bear markets — 4% triggered on normal dips (was 0.04)
    cautious_trailing_stop_pct: float = 0.07      # 7% trailing in CAUTIOUS regime (wider to absorb midcap volatility)
    regime_cautious_threshold_pct: float = -3.0   # gap > -3% from SMA50 → CAUTIOUS (not full BEARISH)
    cautious_buy_score_threshold: float = 0.68    # higher bar for buying in CAUTIOUS regime (lowered from 0.75 — 14% pass rate vs 8%)

    # ── ATR-Based Position Sizing ────────────────────────────────────────
    atr_risk_per_trade_pct: float = 0.01          # risk 1% of portfolio per trade
    atr_stop_multiplier: float = 1.5              # stop-loss at 1.5× ATR below entry

    # ── Scale-Out Profit Taking (two-tier, replaces old single tier) ─────
    profit_take_tier1_pct: float = 0.12           # +12% gain → sell 33% — let winners run further (was 8%: too early for midcaps)
    profit_take_tier1_sell_pct: float = 0.33      # fraction to sell at tier 1
    profit_take_tier2_pct: float = 0.20           # +20% gain → sell another 33% — capture more of big moves (was 15%)
    profit_take_tier2_sell_pct: float = 0.33      # fraction to sell at tier 2

    # ── Sector Concentration (value-based cap) ───────────────────────────
    max_sector_value_pct: float = 0.25            # 25% of portfolio value per sector

    # ── Slippage & Friction Simulation ───────────────────────────────────
    slippage_bps: float = 15.0                    # 15 basis points (0.15%) market impact per trade

    # ── Indian Market Trading Charges (realistic simulation) ─────────────
    # These are deducted from cash on every trade to simulate real costs.
    # Reference: NSE delivery-based equity trading charges (2024-25)
    stt_buy_pct: float = 0.001                    # STT 0.1% on BUY side (delivery)
    stt_sell_pct: float = 0.001                   # STT 0.1% on SELL side (delivery)
    exchange_txn_charge_pct: float = 0.0000345    # NSE transaction charge 0.00345%
    sebi_turnover_fee_pct: float = 0.000001       # SEBI fee 0.0001%
    stamp_duty_buy_pct: float = 0.00015           # Stamp duty 0.015% on BUY side only
    brokerage_per_order: float = 20.0             # Flat brokerage per order (discount broker)
    gst_pct: float = 0.18                         # 18% GST on (brokerage + exchange charges)

    # ── Volume Confirmation Gate ─────────────────────────────────────────
    min_volume_ratio: float = 0.8                  # require volume ≥ 0.8× 20-day avg (relaxed for hourly data)

    # ── Capital Allocation (diversification, not safety) ─────────────────
    max_open_positions: int = 15               # max concurrent holdings
    max_single_trade_pct: float = 0.10         # cap any single trade at 10% of portfolio
    min_cash_reserve_pct: float = 0.05         # tiny 5% emergency buffer

    # ── Batch Decision Thresholds ────────────────────────────────────────
    score_strong_hold: float = 0.40            # score >= this → keep full position — most held stocks score 0.47-0.55, now safely in KEEP zone (was 0.48: caused cascading partial sells)
    score_weak_hold: float = 0.28              # score between weak and strong → partial sell (was 0.35: only truly collapsing stocks should trigger full sell)
    # score < weak_hold → full sell (free capital for better stocks)

    # ── Max Buys Per Cycle (prevents deploying all cash at once) ─────────
    max_buys_per_cycle: int = 3                # max 3 new buys per analysis cycle
    max_buys_per_cycle_cautious: int = 2       # only 2 new buys in CAUTIOUS regime

    # ── Analysis Weights (must sum to 1.0) ───────────────────────────────
    weight_gemini: float = 0.50                # Gemini is primary (has macro/news context)
    weight_ml: float = 0.15                    # ML on hourly data is unreliable (reduced from 0.25)
    weight_news: float = 0.20
    weight_risk: float = 0.15

    @field_validator("watchlist", mode="before")
    @classmethod
    def parse_watchlist(cls, v):
        """Accept NIFTY500, JSON array, comma-separated string, or a list."""
        if isinstance(v, str):
            value = v.strip()
            if value.upper() in {"NIFTY500", "NIFTY_500", "ALL"}:
                return ["NIFTY500"]
            if value.startswith("["):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [str(t).strip() for t in parsed if str(t).strip()]
                except json.JSONDecodeError:
                    pass
            return [
                t.strip().strip('"').strip("'")
                for t in value.split(",")
                if t.strip().strip('"').strip("'")
            ]
        return v

    # ── Dhan Broker API ────────────────────────────────────────────────
    dhan_client_id: str = ""
    dhan_pin: str = ""
    dhan_totp_secret: str = ""
    dhan_access_token: str = ""  # Manual token from api.dhan.co (fallback if TOTP fails)
    dhan_trading_enabled: bool = False  # Master switch for Dhan integration
    live_capital_cap: float = 100000.0  # 0 means Full Investment

    # ── Trading Mode ─────────────────────────────────────────────────
    trading_mode: str = "paper"  # "paper" or "live"

    # ── Global Kill Switch ───────────────────────────────────────────
    kill_switch_enabled: bool = False  # When True: no new BUYs, only SELLs

    # ── Real-Time Market Feed (Dhan WebSocket) ───────────────────────
    realtime_enabled: bool = True              # Master switch for real-time feed
    realtime_candle_intervals: List[str] = ["1m", "5m", "15m"]  # Candle intervals built from ticks
    realtime_tick_batch_ms: int = 250          # Batch frontend WS updates every 250ms
    realtime_subscribe_watchlist: bool = True  # Subscribe to entire NIFTY 500 (not just held stocks)

    # ── Scheduler ────────────────────────────────────────────────────
    scheduler_timezone: str = "Asia/Kolkata"
    market_open_hour: int = 9
    market_open_minute: int = 15
    market_close_hour: int = 15
    market_close_minute: int = 30

    model_config = {
        "env_file": (BASE_DIR / ".env", ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
