import csv
import io
import logging
import urllib.request

logger = logging.getLogger(__name__)

NIFTY500_CONSTITUENTS_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"

NIFTY_STOCKS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS", "INFY.NS", "ITC.NS", "LT.NS", 
    "BAJFINANCE.NS", "HINDUNILVR.NS", "AXISBANK.NS", "KOTAKBANK.NS", "ASIANPAINT.NS", "MARUTI.NS", "SUNPHARMA.NS",
    "TITAN.NS", "ULTRACEMCO.NS", "NTPC.NS", "TATASTEEL.NS", "M&M.NS", "POWERGRID.NS", "ONGC.NS", "COALINDIA.NS",
    "ADANIENT.NS", "ADANIPORTS.NS", "WIPRO.NS", "HCLTECH.NS", "BAJAJFINSV.NS", "NESTLEIND.NS", "JSWSTEEL.NS",
    "GRASIM.NS", "HINDALCO.NS", "TECHM.NS", "TATAMOTORS.NS", "SBILIFE.NS", "HDFCLIFE.NS", "DIVISLAB.NS",
    "DRREDDY.NS", "CIPLA.NS", "APOLLOHOSP.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS", "HEROMOTOCO.NS", "BRITANNIA.NS",
    "TATACONSUM.NS", "UPL.NS", "INDUSINDBK.NS", "SBIN.NS", "BPCL.NS", "SHREECEM.NS", "TRENT.NS", "BEL.NS",
    "HAL.NS", "ZOMATO.NS", "JIOFIN.NS", "DMART.NS", "CHOLAFIN.NS", "PIIND.NS", "TVSMOTOR.NS", "LTIM.NS",
    "PIDILITIND.NS", "SRF.NS", "HAVELLS.NS", "ABB.NS", "SIEMENS.NS", "CUMMINSIND.NS", "VOLTAS.NS", "DIXON.NS",
    "POLYCAB.NS", "CGPOWER.NS", "BHEL.NS", "RVNL.NS", "IRFC.NS", "PFC.NS", "RECLTD.NS", "IREDA.NS",
    "NHPC.NS", "SJVN.NS", "SUZLON.NS", "TATACHEM.NS", "TATAPOWER.NS", "ADANIPOWER.NS", "ADANIGREEN.NS",
    "ATGL.NS", "AWL.NS", "NYKAA.NS", "PAYTM.NS", "DELHIVERY.NS", "PBFINTECH.NS", "POLICYBZR.NS", "MUTHOOTFIN.NS",
    "MANAPPURAM.NS", "M&MFIN.NS", "LICHSGFIN.NS", "CANBK.NS", "PNB.NS", "BANKBARODA.NS", "UNIONBANK.NS", "IOB.NS"
]

_cached_nifty500: list[str] | None = None


def resolve_watchlist(configured_watchlist: list[str]) -> list[str]:
    """Resolve WATCHLIST=NIFTY500 to the current Nifty 500 Yahoo tickers."""
    if (
        len(configured_watchlist) == 1
        and configured_watchlist[0].upper() in {"NIFTY500", "NIFTY_500", "ALL"}
    ):
        return load_nifty500_watchlist()
    return configured_watchlist


def load_nifty500_watchlist() -> list[str]:
    """
    Fetch current Nifty 500 constituents from Nifty Indices.
    Falls back to the bundled list if the public CSV is temporarily unavailable.
    """
    global _cached_nifty500
    if _cached_nifty500:
        return _cached_nifty500

    try:
        request = urllib.request.Request(
            NIFTY500_CONSTITUENTS_URL,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            csv_text = response.read().decode("utf-8-sig")

        reader = csv.DictReader(io.StringIO(csv_text))
        symbols = []
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            if symbol:
                symbols.append(f"{symbol.replace(' ', '')}.NS")

        if symbols:
            _cached_nifty500 = symbols
            logger.info(f"Loaded {len(symbols)} Nifty 500 symbols from Nifty Indices.")
            return symbols
    except Exception as e:
        logger.warning(f"Could not load Nifty 500 CSV, using bundled fallback: {e}")

    return NIFTY_STOCKS
