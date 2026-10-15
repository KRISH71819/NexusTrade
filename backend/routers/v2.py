"""
V2 API Router — Strict Global Contract
All 9 core endpoints inject `db_online` and `kill_switch_active` telemetry.
This router wraps existing V1 logic to guarantee a unified response envelope 
without duplicating business logic.
"""
import logging
from fastapi import APIRouter, Query
from typing import Optional

from database import ensure_connected
from kill_switch import is_kill_switch_on
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["V2 Core"])

async def _get_telemetry() -> dict:
    """Inject strict global contract telemetry."""
    try:
        db_online = await ensure_connected()
    except Exception:
        db_online = False
        
    try:
        # is_kill_switch_on internally failsafe to True if DB is unreachable
        kill_switch_active = await is_kill_switch_on()
    except Exception:
        kill_switch_active = True 
        
    return {
        "db_online": bool(db_online),
        "kill_switch_active": bool(kill_switch_active),
    }

# ── 1. System Health ────────────────────────────────────────────────────────
@router.get("/health")
async def v2_health():
    telemetry = await _get_telemetry()
    feed_connected = False
    try:
        from market_feed import is_feed_connected
        feed_connected = is_feed_connected()
    except Exception:
        pass
        
    return {
        "status": "healthy" if telemetry["db_online"] else "degraded",
        "service": "NexusTrade AI Agent V2",
        "market": "NSE/BSE (India)",
        "trading_mode": settings.trading_mode,
        "legacy_engine_enabled": settings.legacy_engine_enabled,
        "meta_portfolio_enabled": settings.meta_portfolio_enabled,
        "realtime_feed": feed_connected,
        **telemetry
    }

# ── 2. Portfolio State ──────────────────────────────────────────────────────
@router.get("/portfolio")
async def v2_portfolio(mode: Optional[str] = Query(default=None)):
    from routers.portfolio import get_portfolio_state
    telemetry = await _get_telemetry()
    data = await get_portfolio_state(mode)
    return {**data, **telemetry}

# ── 3. Portfolio History ────────────────────────────────────────────────────
@router.get("/portfolio/history")
async def v2_portfolio_history(limit: int = Query(100, ge=1, le=1000), mode: Optional[str] = Query(default=None)):
    from routers.portfolio import get_portfolio_history
    telemetry = await _get_telemetry()
    data = await get_portfolio_history(limit, mode)
    return {**data, **telemetry}

# ── 4. Latest Analyses ──────────────────────────────────────────────────────
@router.get("/analysis/latest")
async def v2_analysis_latest():
    from routers.analysis import get_latest_analyses
    telemetry = await _get_telemetry()
    data = await get_latest_analyses()
    return {**data, **telemetry}

# ── 5. Trades ───────────────────────────────────────────────────────────────
@router.get("/trades")
async def v2_trades(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    mode: Optional[str] = Query(default=None)
):
    from routers.trades import get_all_trades
    telemetry = await _get_telemetry()
    data = await get_all_trades(limit, offset, mode)
    return {**data, **telemetry}

# ── 6. News Intelligence ────────────────────────────────────────────────────
@router.get("/news")
async def v2_news():
    from routers.news import get_latest_news
    telemetry = await _get_telemetry()
    data = await get_latest_news()
    return {**data, **telemetry}

# ── 7. Analytics PnL ────────────────────────────────────────────────────────
@router.get("/analytics/pnl")
async def v2_analytics_pnl(mode: Optional[str] = Query(default=None)):
    from routers.analytics import get_pnl_analytics
    telemetry = await _get_telemetry()
    data = await get_pnl_analytics(mode)
    return {**data, **telemetry}

# ── 8. Meta Portfolio Summary ───────────────────────────────────────────────
@router.get("/meta/summary")
async def v2_meta_summary():
    from routers.meta import meta_summary
    telemetry = await _get_telemetry()
    data = await meta_summary()
    # Telemetry dict is unpacked second to strictly overwrite any legacy keys
    return {**data, **telemetry}

# ── 9. Trading Status ───────────────────────────────────────────────────────
@router.get("/trading/status")
async def v2_trading_status():
    from routers.trading_mode import get_trading_mode
    telemetry = await _get_telemetry()
    data = await get_trading_mode()
    return {**data, **telemetry}
