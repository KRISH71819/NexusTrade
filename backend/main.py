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
    """Startup: connect DB + seed portfolio + start scheduler.  Shutdown: close all."""
    logger.info("Starting Paper Trading Agent...")
    await connect_db()
    logger.info("Database connected and portfolio initialized.")
    start_scheduler()
    if settings.run_analysis_on_startup:
        asyncio.create_task(_run_startup_analysis())
    yield
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
    title="Paper Trading Swing-Trading AI Agent",
    description="Transparent AI-powered paper trading for NSE/BSE Indian stocks",
    version="1.0.0",
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

from routers import portfolio, trades, analysis, market, news, analytics  # noqa: E402

app.include_router(portfolio.router, prefix="/api", tags=["Portfolio"])
app.include_router(trades.router, prefix="/api", tags=["Trades"])
app.include_router(analysis.router, prefix="/api", tags=["Analysis"])
app.include_router(market.router, prefix="/api", tags=["Market Data"])
app.include_router(news.router, prefix="/api", tags=["News Intelligence"])
app.include_router(analytics.router, prefix="/api", tags=["Analytics"])


# ── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health", tags=["System"])
async def health_check():
    return {
        "status": "healthy",
        "service": "Paper Trading AI Agent",
        "market": "NSE/BSE (India)",
    }


@app.post("/api/trigger-analysis", tags=["System"])
async def trigger_analysis():
    """Manually trigger a full analysis cycle. Force-cancels any running cycle."""
    results = await run_analysis_cycle(force=True)
    return {
        "message": "Analysis cycle completed",
        "results": results,
    }
