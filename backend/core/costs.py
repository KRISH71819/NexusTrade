"""Indian delivery-equity cost model (ported verbatim math from legacy ledger.py).
Pure: rates are arguments with frozen defaults, never imported from config."""
RATES = {
    "stt_buy_pct": 0.001,            # STT 0.1% buy
    "stt_sell_pct": 0.001,           # STT 0.1% sell
    "exchange_txn_charge_pct": 0.0000345,
    "sebi_turnover_fee_pct": 0.000001,
    "stamp_duty_buy_pct": 0.00015,   # stamp 0.015% buy only
    "brokerage_per_order": 20.0,     # Rs 20 per order
    "dp_charge_per_sell": 15.0,      # Rs 15 per scrip, sell only
    "gst_pct": 0.18,                 # GST on brokerage + txn + DP
}
DEFAULT_SLIPPAGE_BPS = 40.0

def calculate_trade_charges(turnover: float, side: str, rates: dict | None = None) -> dict:
    r = rates or RATES
    stt = turnover * (r["stt_buy_pct"] if side == "BUY" else r["stt_sell_pct"])
    exchange_txn = turnover * r["exchange_txn_charge_pct"]
    sebi_fee = turnover * r["sebi_turnover_fee_pct"]
    stamp_duty = turnover * r["stamp_duty_buy_pct"] if side == "BUY" else 0.0
    brokerage = r["brokerage_per_order"]
    dp_charge = r["dp_charge_per_sell"] if side == "SELL" else 0.0
    gst = (brokerage + exchange_txn + dp_charge) * r["gst_pct"]
    total = stt + exchange_txn + sebi_fee + stamp_duty + brokerage + dp_charge + gst
    return {
        "stt": round(stt, 2), "exchange_txn": round(exchange_txn, 2),
        "sebi_fee": round(sebi_fee, 2), "stamp_duty": round(stamp_duty, 2),
        "brokerage": round(brokerage, 2), "dp_charge": round(dp_charge, 2),
        "gst": round(gst, 2), "total_charges": round(total, 2),
    }

def apply_slippage(price: float, side: str, slippage_bps: float = DEFAULT_SLIPPAGE_BPS) -> float:
    mult = 1 + slippage_bps / 10_000 if side == "BUY" else 1 - slippage_bps / 10_000
    return round(price * mult, 2)

def one_side_cost_rate(reference_turnover: float = 100_000.0,
                       slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
                       rates: dict | None = None) -> float:
    """Average one-side charge rate + slippage. Used by the sandbox."""
    buy = calculate_trade_charges(reference_turnover, "BUY", rates)["total_charges"] / reference_turnover
    sell = calculate_trade_charges(reference_turnover, "SELL", rates)["total_charges"] / reference_turnover
    return (buy + sell) / 2.0 + slippage_bps / 10_000.0
