"""
System B switchover freeze tests (Section 4.2).

(a) legacy_engine_enabled=False → hourly cycle early-returns with ZERO
    LLM / screener / batch-optimizer calls
(b) the 30-min rule-based risk check still executes with the flag False
(c) WebSocket start is skipped with the flag False (+ exact log line)
(d) POST /api/trigger-analysis returns the frozen message and does nothing
(e) the weekly meta Telegram report builder emits meta numbers (mocked DB)
(f) research trigger clamps count to the cap; Saturday job gated by flag
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── (a) Hourly analysis cycle frozen ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_hourly_cycle_frozen_performs_zero_work():
    import scheduler

    with patch.object(scheduler.settings, "legacy_engine_enabled", False), \
         patch("scheduler.analyze_with_llm", new_callable=AsyncMock) as m_llm, \
         patch("scheduler.bulk_screener") as m_screen, \
         patch("scheduler._execute_batch_decisions", new_callable=AsyncMock) as m_batch:

        result = await scheduler.run_analysis_cycle()

        assert result == {"status": "legacy_frozen"}
        m_llm.assert_not_awaited()
        m_batch.assert_not_awaited()
        m_screen.assert_not_called()


@pytest.mark.asyncio
async def test_hourly_cycle_runs_when_legacy_enabled():
    """Sanity: with the flag ON the guard must not fire."""
    import scheduler

    portfolio = {"cash": 100_000.0, "total_value": 1_000_000.0, "holdings": []}

    with patch.object(scheduler.settings, "legacy_engine_enabled", True), \
         patch("scheduler.stamp_day_open_if_needed", new_callable=AsyncMock), \
         patch("scheduler.get_daily_pnl_pct",
               new_callable=AsyncMock, return_value=0.0), \
         patch("scheduler.get_portfolio_for_mode", new_callable=AsyncMock) as m_port, \
         patch("scheduler.resolve_watchlist", return_value=["TCS.NS"]), \
         patch("scheduler.analyze_with_llm", new_callable=AsyncMock), \
         patch("scheduler.record_score_snapshot", new_callable=AsyncMock), \
         patch("scheduler.fetch_market_regime",
               return_value={"regime": "BULLISH", "nifty_close": 25000.0,
                             "nifty_sma50": 24000.0, "gap_pct": 4.0}):
        m_port.return_value = portfolio
        # bulk_screener runs in a thread — make it return no candidates so the
        # cycle ends immediately after the guard.
        with patch("scheduler.bulk_screener", return_value=[]):
            result = await scheduler.run_analysis_cycle()

        assert result["status"] == "completed"
        assert result["tickers_analyzed"] == 0


# ── (b) Risk check stays active while frozen ─────────────────────────────────

@pytest.mark.asyncio
async def test_risk_check_executes_when_frozen():
    import scheduler

    portfolio = {
        "cash": 50_000.0,
        "total_value": 150_000.0,
        "holdings": [{"ticker": "TCS.NS", "quantity": 10, "avg_price": 3500.0,
                      "peak_price": 3600.0}],
    }

    with patch.object(scheduler.settings, "legacy_engine_enabled", False), \
         patch("scheduler.get_portfolio_for_mode",
               new_callable=AsyncMock, return_value=portfolio), \
         patch("market_feed.is_feed_connected", return_value=False), \
         patch("scheduler.get_batch_prices",
               return_value={"TCS.NS": 3200.0}), \
         patch("scheduler.update_portfolio_valuation", new_callable=AsyncMock) as m_val, \
         patch("scheduler.fetch_market_regime",
               return_value={"regime": "BULLISH", "nifty_close": 25000.0,
                             "nifty_sma50": 24000.0, "gap_pct": 4.0}), \
         patch("scheduler.check_stop_losses",
               return_value=[{"ticker": "TCS.NS", "price": 3200.0,
                              "reason": "STOP-LOSS hit"}]), \
         patch("scheduler.execute_sell_for_mode",
               new_callable=AsyncMock, return_value={"status": "sold"}), \
         patch("scheduler.send_trade_alert", new_callable=AsyncMock), \
         patch("scheduler.detect_underperformers", return_value=[]), \
         patch("scheduler.check_profit_taking", return_value=[]):

        await scheduler.run_risk_check()

        m_val.assert_awaited_once()
        scheduler.execute_sell_for_mode.assert_awaited_once()


# ── (c) WebSocket start skipped when frozen ──────────────────────────────────

@pytest.mark.asyncio
async def test_websocket_start_skipped_when_frozen(caplog):
    import market_feed

    market_feed._feed_running = False

    with patch.object(market_feed.settings, "legacy_engine_enabled", False):
        with patch("market_feed.threading.Thread") as m_thread:
            with caplog.at_level("INFO", logger="market_feed"):
                await market_feed.start_market_feed()

            m_thread.assert_not_called()
            market_feed._feed_running = False  # defensive

    assert ("WebSocket disabled (legacy frozen) — risk checks use polling"
            in caplog.text)


# ── (d) Manual analyze endpoint frozen ───────────────────────────────────────

def test_trigger_analysis_returns_frozen_message():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    with patch.object(main.settings, "legacy_engine_enabled", False):
        resp = client.post("/api/trigger-analysis")

    assert resp.status_code == 200
    assert resp.json() == {"status": "legacy frozen"}


# ── (e) Weekly meta report builder (mocked DB) ───────────────────────────────

class _FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    def to_list(self, length=None):
        async def _run():
            return self._docs
        return _run()


def _fake_coll(find_docs=None, one_doc=None):
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=one_doc)

    async def _find_one(*a, **k):
        return one_doc
    coll.find_one = _find_one
    coll.find = MagicMock(return_value=_FakeQuery(find_docs or []))
    return coll


@pytest.mark.asyncio
async def test_weekly_summary_contains_meta_numbers():
    import reporting

    meta_doc = {
        "_id": "meta",
        "total_value": 1_100_000.0,
        "cash": 100_000.0,
        "holdings_value": 1_000_000.0,
        "holdings": [
            {"ticker": "A.NS", "quantity": 10, "avg_price": 100.0,
             "current_price": 110.0, "market_value": 500_000.0},
            {"ticker": "B.NS", "quantity": 10, "avg_price": 100.0,
             "current_price": 110.0, "market_value": 500_000.0},
        ],
        "strat_info": {"vol_scale": 0.8, "exposure": 0.7, "trend_on": True},
        "reconciliation": {"target_exposure": 0.7, "names_target": 25},
        "last_rebalance": "2026-08-20",
        "peak_value": 1_100_000.0,
    }
    equity_docs = [
        {"date": "2026-08-15", "timestamp": "2026-08-15T10:00:00+00:00",
         "total_value": 1_000_000.0},
        {"date": "2026-08-21", "timestamp": "2026-08-21T10:00:00+00:00",
         "total_value": 1_050_000.0},
        {"date": "2026-08-22", "timestamp": "2026-08-22T10:00:00+00:00",
         "total_value": 1_100_000.0},
    ]

    meta_coll = _fake_coll(find_docs=[], one_doc=meta_doc)
    eq_coll = _fake_coll(find_docs=equity_docs, one_doc=None)

    with patch("database.get_meta_portfolio_collection", return_value=meta_coll), \
         patch("database.get_meta_equity_collection", return_value=eq_coll), \
         patch("alpha_sandbox.hall_of_fame.list_hall_of_fame",
               new_callable=AsyncMock, return_value=[
                   {"name": "hof_alpha_1", "promoted_at": "2026-08-21T09:00:00"},
               ]), \
         patch("llm_engine.get_daily_budget_status",
               return_value={"calls_today": 7, "daily_limit": 1250}), \
         patch.object(reporting, "_get_legacy_status_line",
                      new_callable=AsyncMock,
                      return_value="3 open position(s) | day -0.50% "
                                   "| risk=OK [FROZEN]"):

        summary = await reporting.build_weekly_summary(send_to_telegram=False)

    assert "System B" in summary
    assert "Rs.1,100,000" in summary or "Rs.1.1" in summary
    assert "+100,000" in summary          # weekly P&L vs oldest-in-window snapshot
    assert "Hall of Fame" in summary
    assert "+1 this week" in summary      # one addition this week
    assert "[FROZEN]" in summary          # legacy line present
    assert "LLM budget today: 7/1250" in summary


@pytest.mark.asyncio
async def test_daily_report_meta_primary_and_one_legacy_line():
    import reporting

    with patch.object(reporting, "_get_meta_primary_lines",
                      new_callable=AsyncMock, return_value=[
                          "  Value: Rs.   1,100,000.00  (+10.00% since inception)",
                      ]), \
         patch.object(reporting, "_get_legacy_status_line",
                      new_callable=AsyncMock,
                      return_value="3 open position(s) | risk=OK [FROZEN]"), \
         patch("kill_switch.is_kill_switch_on",
               new_callable=AsyncMock, return_value=False), \
         patch("telegram_bot.send_message", new_callable=AsyncMock):

        report = await reporting.generate_daily_report()

    assert "SYSTEM B — META RESEARCH PORTFOLIO" in report
    assert "LEGACY (SYSTEM A)" in report
    # Legacy section is exactly ONE status line + kill switch line
    # (separator rules excluded).
    legacy_block = report.split("LEGACY (SYSTEM A)")[1]
    content_lines = [
        l for l in legacy_block.strip().splitlines()
        if l.strip()
        and not l.strip().startswith("==")
        and not l.strip().startswith("─")
    ]
    assert len(content_lines) == 2


# ── (f) Research trigger clamp + Saturday gating ─────────────────────────────

@pytest.mark.asyncio
async def test_research_trigger_clamps_count():
    import evolution_driver

    with patch.object(evolution_driver, "run_scoped_evolution",
                      new_callable=AsyncMock) as m_run:
        result = evolution_driver.start_batch(count=99)
        try:
            assert result["status"] == "started"
            assert result["count"] == 6  # hard cap
            assert result["run_id"]
            # Let the background task finish (mocked).
            pending = [t for t in list(evolution_driver._background_tasks)]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            evolution_driver._background_tasks.clear()


@pytest.mark.asyncio
async def test_research_trigger_rejects_concurrent_run():
    import evolution_driver

    evolution_driver._current_run_id = "fake-run-id"  # simulate running batch
    try:
        result = evolution_driver.start_batch(count=2)
        assert result["status"] == "already_running"
        assert result["run_id"] == "fake-run-id"
    finally:
        evolution_driver._current_run_id = None


@pytest.mark.asyncio
async def test_saturday_job_gated_by_auto_flag():
    import scheduler

    with patch.object(scheduler.settings, "evolution_auto_enabled", False), \
         patch.object(scheduler.settings, "meta_portfolio_enabled", True):
        sched = scheduler.start_scheduler()
        try:
            ids = [j.id for j in sched.get_jobs()]
        finally:
            scheduler.stop_scheduler()

    assert "saturday_evolution" not in ids
    assert "sunday_weekly_summary" in ids
    assert "meta_mtm" in ids
    assert "meta_rebalance" in ids


@pytest.mark.asyncio
async def test_saturday_job_registered_when_auto_enabled():
    import scheduler

    with patch.object(scheduler.settings, "evolution_auto_enabled", True), \
         patch.object(scheduler.settings, "meta_portfolio_enabled", True):
        sched = scheduler.start_scheduler()
        try:
            ids = [j.id for j in sched.get_jobs()]
        finally:
            scheduler.stop_scheduler()

    assert "saturday_evolution" in ids


def test_config_defaults_switchover():
    from config import Settings

    s = Settings(mongodb_uri="mongodb://localhost:27017")
    assert s.meta_portfolio_enabled is True
    assert s.legacy_engine_enabled is False
    assert s.evolution_auto_enabled is False
    assert s.evolution_max_candidates == 6
    assert s.evolution_time_cap_min == 50
