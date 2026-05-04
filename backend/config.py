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
    gemini_model: str = "gemini-2.5-pro"

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
    max_candidates_for_ai: int = 10
    run_analysis_on_startup: bool = True

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

    # ── Scheduler ────────────────────────────────────────────────────────
    scheduler_timezone: str = "Asia/Kolkata"
    market_open_hour: int = 9
    market_open_minute: int = 15
    market_close_hour: int = 15
    market_close_minute: int = 30

    # ── ML Thresholds ────────────────────────────────────────────────────
    ml_buy_threshold: float = 0.65
    ml_sell_threshold: float = 0.35
    llm_buy_threshold: float = 0.3
    llm_sell_threshold: float = -0.3

    model_config = {
        "env_file": (BASE_DIR / ".env", ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

