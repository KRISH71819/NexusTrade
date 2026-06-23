"""
FastAPI application — entrypoint with lifespan, CORS, and router mounting.
"""

from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys

from config import settings
from database import connect_db, close_db
from scheduler import start_scheduler, stop_scheduler, run_analysis_cycle

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect DB + seed portfolio + init Dhan + start scheduler.  Shutdown: close all."""
    logger.info("Starting NexusTrade Agent...")
    await connect_db()
    logger.info("Database connected and portfolio initialized.")

    # Initialize kill switch
    from kill_switch import initialize_kill_switch
    await initialize_kill_switch()

    # Initialize Dhan client from saved credentials (MongoDB) or .env
    from dhan_client import dhan_client
    from routers.trading_mode import load_and_configure_dhan

    # Try loading credentials saved from the UI (MongoDB)
    db_loaded = await load_and_configure_dhan()
    if db_loaded:
        logger.info("Dhan credentials loaded from database")

    # If credentials are available (from DB or .env), try to connect
    if dhan_client.is_configured:
        from security_master import security_master
        dhan_init = await dhan_client.initialize()
        logger.info(
            f"Dhan client: {dhan_init.get('status', 'unknown')}"
        )
        sec_init = await security_master.load()
        logger.info(
            f"Security master: {sec_init.get('status', 'unknown')} "
            f"({sec_init.get('count', 0)} instruments)"
        )

        # Start real-time market feed (Dhan WebSocket)
        from market_feed import start_market_feed
        await start_market_feed()
    else:
        logger.info("Dhan not configured — paper-only mode")

    start_scheduler()
    if settings.run_analysis_on_startup:
        asyncio.create_task(_run_startup_analysis())
    yield
    # Shutdown
    from market_feed import stop_market_feed
    await stop_market_feed()
    stop_scheduler()
    await close_db()
    logger.info("Shutdown complete.")


async def _run_startup_analysis():
    """Run one non-blocking analysis pass after startup so the dashboard fills itself."""
    await asyncio.sleep(3)
    try:
        logger.info("Startup auto-analysis enabled; running first scan.")
        await run_analysis_cycle()
    except Exception:
        logger.exception("Startup auto-analysis failed.")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NexusTrade AI Agent",
    description="AI-powered paper & live trading for NSE/BSE Indian stocks",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend from various deployment targets
import os
_cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://nexus-trade-gamma.vercel.app",
]
# Add any custom frontend URL from env
_frontend_url = os.environ.get("FRONTEND_URL", "")
if _frontend_url:
    _cors_origins.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://.*\.hf\.space",  # all HuggingFace Spaces
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Mount Routers ────────────────────────────────────────────────────────────

from routers import portfolio, trades, analysis, market, news, analytics, trading_mode  # noqa: E402
from routers import realtime  # noqa: E402

app.include_router(portfolio.router, prefix="/api", tags=["Portfolio"])
app.include_router(trades.router, prefix="/api", tags=["Trades"])
app.include_router(analysis.router, prefix="/api", tags=["Analysis"])
app.include_router(market.router, prefix="/api", tags=["Market Data"])
app.include_router(news.router, prefix="/api", tags=["News Intelligence"])
app.include_router(analytics.router, prefix="/api", tags=["Analytics"])
app.include_router(trading_mode.router, prefix="/api", tags=["Trading Mode"])
app.include_router(realtime.router, prefix="/api", tags=["Real-Time Feed"])


# ── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health", tags=["System"])
async def health_check():
    from kill_switch import is_kill_switch_on
    from market_feed import is_feed_connected
    kill_switch = await is_kill_switch_on()
    return {
        "status": "healthy",
        "service": "NexusTrade AI Agent",
        "market": "NSE/BSE (India)",
        "trading_mode": settings.trading_mode,
        "kill_switch_active": kill_switch,
        "dhan_enabled": settings.dhan_trading_enabled,
        "realtime_feed": is_feed_connected(),
    }


@app.post("/api/trigger-analysis", tags=["System"])
async def trigger_analysis():
    """Manually trigger a full analysis cycle. Force-cancels any running cycle."""
    results = await run_analysis_cycle(force=True)
    return {
        "message": "Analysis cycle completed",
        "results": results,
    }
