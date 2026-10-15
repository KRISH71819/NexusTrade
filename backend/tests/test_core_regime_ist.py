import re
from core.regime import code_regime, apply_bounded_override, exposure_cap
from core.ist import ist_today_str, is_stale_day_open

def test_crisis_triggers():
    assert code_regime(34, 1.0, 40)["state"] == "CRISIS"
    assert code_regime(60, -7.0, 40)["state"] == "CRISIS"
    assert code_regime(60, 1.0, 91)["state"] == "CRISIS"
    assert exposure_cap("CRISIS") == 0.0

def test_ladder():
    assert code_regime(70, 2.0, 40)["state"] == "RISK_ON"
    assert code_regime(50, -1.0, 50)["state"] == "NEUTRAL"
    assert code_regime(40, -1.0, 50)["state"] == "RISK_OFF"
    assert code_regime(50, -4.0, 50)["state"] == "RISK_OFF"
    assert exposure_cap("NEUTRAL") == 0.8

def test_bounded_override():
    assert apply_bounded_override("NEUTRAL", "RISK_ON") == ("RISK_ON", True)
    assert apply_bounded_override("NEUTRAL", "CRISIS") == ("NEUTRAL", False)
    assert apply_bounded_override("RISK_ON", "RISK_OFF") == ("RISK_ON", False)
    assert apply_bounded_override("NEUTRAL", "GARBAGE") == ("NEUTRAL", False)

def test_ist_helpers():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ist_today_str())
    assert is_stale_day_open("2026-10-14", "2026-10-15") is True
    assert is_stale_day_open("2026-10-15", "2026-10-15") is False
    assert is_stale_day_open(None, "2026-10-15") is True
