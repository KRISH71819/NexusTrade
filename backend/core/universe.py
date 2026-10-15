"""Qualified-universe predicates (pure). Data loading lives in the data layer (P2)."""
MIN_BARS = 1500
MIN_MEDIAN_TURNOVER = 5.0e7   # Rs 5 Cr, 20d median
MIN_PRICE = 50.0
PANEL_MULT = 4                # research panel >= 4x top_n

DELISTED_SYMBOLS = frozenset({
    "SWANENERGY.NS", "WELSPUNIND.NS", "SEQUENT.NS", "DPABOILERS.NS",
    "TCNSBRANDS.NS", "RAMAKRISHNA.NS", "AMAREJABAT.NS", "LTIM.NS",
    "L&TFH.NS", "SUVENPHAR.NS", "AEGISCHEM.NS", "PROCTER.NS",
    "ZOMATO.NS", "PEL.NS", "ISEC.NS", "MAINDREAL.NS",
    "NIPPONLIFE.NS", "VINATIORG.NS", "GMRINFRA.NS", "IBULHSGFIN.NS",
    "IDFC.NS", "PBFINTECH.NS", "CENTURYTEX.NS", "UNOINDA.NS",
    "MCDOWELL-N.NS", "NUVAM.NS",
})

def is_qualified(bars: int, median_turnover_20d: float, last_price: float) -> bool:
    return (bars >= MIN_BARS and median_turnover_20d >= MIN_MEDIAN_TURNOVER
            and last_price >= MIN_PRICE)

def panel_size_ok(panel_n: int, top_n: int) -> bool:
    """Mistake-ledger #6 guard: a tiny panel collapses every candidate into the
    same hold-all portfolio. Refuse instead of silently researching garbage."""
    return panel_n >= PANEL_MULT * top_n
