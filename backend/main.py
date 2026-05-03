"""
FastAPI application — entrypoint with lifespan, CORS, and router mounting.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys

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
    yield
    stop_scheduler()
    await close_db()
    logger.info("Shutdown complete.")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Paper Trading Swing-Trading AI Agent",
    description="Transparent AI-powered paper trading for NSE/BSE Indian stocks",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Mount Routers ────────────────────────────────────────────────────────────

from routers import portfolio, trades, analysis, market  # noqa: E402

app.include_router(portfolio.router, prefix="/api", tags=["Portfolio"])
app.include_router(trades.router, prefix="/api", tags=["Trades"])
app.include_router(analysis.router, prefix="/api", tags=["Analysis"])
app.include_router(market.router, prefix="/api", tags=["Market Data"])


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
    """Manually trigger a full analysis cycle for all watchlist tickers."""
    results = await run_analysis_cycle()
    return {
        "message": "Analysis cycle completed",
        "results": results,
    }
