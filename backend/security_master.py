"""
Security Master — maps NSE ticker symbols to Dhan security IDs.

Downloads and caches the Dhan scrip master CSV which contains the mapping
between trading symbols (e.g., RELIANCE) and Dhan's internal security IDs.

The CSV is refreshed daily (Dhan updates it around 8:30 AM IST).
"""

import io
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# Dhan scrip master CSV URL (compact version — ~2MB)
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Cache TTL — refresh every 12 hours
CACHE_TTL_SECONDS = 12 * 3600


class SecurityMaster:
    """Maps NSE ticker symbols to Dhan security IDs."""

    def __init__(self):
        self._nse_equity_map: dict[str, str] = {}  # ticker -> security_id
        self._security_to_ticker: dict[str, str] = {}  # security_id -> ticker
        self._last_load_time: float = 0
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded and len(self._nse_equity_map) > 0

    async def load(self, force: bool = False) -> dict:
        """Download and parse the Dhan scrip master CSV."""
        now = time.monotonic()
        if not force and self._loaded and (now - self._last_load_time) < CACHE_TTL_SECONDS:
            return {"status": "cached", "count": len(self._nse_equity_map)}

        try:
            logger.info("Downloading Dhan scrip master CSV...")
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(SCRIP_MASTER_URL)
                response.raise_for_status()

            # Parse CSV
            df = pd.read_csv(io.StringIO(response.text), low_memory=False)
            logger.info(f"Scrip master CSV loaded: {len(df)} rows, columns: {list(df.columns)[:10]}")

            # Filter for NSE Equity only
            # Column names: SEM_EXM_EXCH_ID, SEM_SEGMENT, SEM_TRADING_SYMBOL, SEM_SMST_SECURITY_ID
            nse_equity = df[
                (df["SEM_EXM_EXCH_ID"] == "NSE")
                & (df["SEM_SEGMENT"] == "E")  # E = Equity
            ].copy()

            if nse_equity.empty:
                logger.warning("No NSE equity records found in scrip master!")
                return {"status": "error", "error": "No NSE equity records found"}

            # Build the mapping: clean ticker symbol -> security_id
            new_map = {}
            reverse_map = {}
            for _, row in nse_equity.iterrows():
                symbol = str(row.get("SEM_TRADING_SYMBOL", "")).strip()
                security_id = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()

                if symbol and security_id and security_id != "nan":
                    # Clean the symbol — remove any suffixes like "-EQ"
                    clean_symbol = symbol.replace("-EQ", "").replace("-BE", "").strip().upper()
                    new_map[clean_symbol] = security_id
                    reverse_map[security_id] = clean_symbol

            self._nse_equity_map = new_map
            self._security_to_ticker = reverse_map
            self._last_load_time = now
            self._loaded = True

            logger.info(f"Security master loaded: {len(new_map)} NSE equity instruments")
            return {"status": "loaded", "count": len(new_map)}

        except Exception as e:
            logger.error(f"Failed to load scrip master: {e}")
            return {"status": "error", "error": str(e)}

    def get_security_id(self, ticker: str) -> Optional[str]:
        """
        Get Dhan security ID for an NSE ticker symbol.

        Handles common transformations:
          - "RELIANCE.NS" -> "RELIANCE"
          - "TCS" -> looks up in mapping
          - "NIFTY 50" -> not in equity map (index)
        """
        if not self._loaded:
            logger.warning("Security master not loaded — call load() first")
            return None

        # Clean the ticker
        clean = ticker.strip().upper()
        # Remove yfinance suffixes
        if clean.endswith(".NS"):
            clean = clean[:-3]
        if clean.endswith(".BO"):
            clean = clean[:-3]

        # Direct lookup
        sid = self._nse_equity_map.get(clean)
        if sid:
            return sid

        # Try with -EQ suffix (some tickers in Dhan include it)
        sid = self._nse_equity_map.get(f"{clean}-EQ")
        if sid:
            return sid

        logger.warning(f"Security ID not found for ticker: {ticker} (cleaned: {clean})")
        return None

    def get_ticker(self, security_id: str) -> Optional[str]:
        """Reverse lookup: security ID -> ticker symbol."""
        return self._security_to_ticker.get(str(security_id))

    def get_stats(self) -> dict:
        """Get cache statistics."""
        return {
            "loaded": self._loaded,
            "count": len(self._nse_equity_map),
            "last_load": datetime.fromtimestamp(
                self._last_load_time, tz=timezone.utc
            ).isoformat() if self._last_load_time else None,
        }


# ── Module-level singleton ───────────────────────────────────────────────────
security_master = SecurityMaster()
