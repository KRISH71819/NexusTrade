"""
LLM Config API — view and toggle multi-agent chain settings.

GET  /api/llm-config       → current mode, models, API key status
PUT  /api/llm-config       → update llm_mode (single/chain)
GET  /api/llm-config/usage → daily call counts + review stats
"""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from config import settings
from llm_engine import get_daily_budget_status, get_review_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm-config", tags=["llm-config"])


class LLMConfigResponse(BaseModel):
    mode: str
    analyst_model: str
    analyst_model_label: str
    reviewer_model: str
    reviewer_model_label: str
    kimi_api_key_configured: bool
    gemini_api_key_configured: bool
    effective_mode: str  # what's actually running (may differ from mode if no kimi key)


class LLMConfigUpdate(BaseModel):
    mode: Optional[str] = None  # "single" or "chain"


class LLMUsageResponse(BaseModel):
    gemma_calls_today: int
    gemma_daily_limit: int
    kimi_calls_today: int
    kimi_model: str
    review_stats: dict


@router.get("", response_model=LLMConfigResponse)
async def get_llm_config():
    """Return current LLM chain configuration."""
    effective_mode = settings.llm_mode
    if effective_mode == "chain" and not settings.kimi_api_key:
        effective_mode = "single"  # auto-degraded

    return LLMConfigResponse(
        mode=settings.llm_mode,
        analyst_model=settings.kimi_model if effective_mode == "chain" else settings.gemini_model,
        analyst_model_label="Kimi K3 (2.8T)" if effective_mode == "chain" else "Gemma 4 (31B)",
        reviewer_model=settings.gemini_model if effective_mode == "chain" else "N/A",
        reviewer_model_label="Gemma 4 (31B)" if effective_mode == "chain" else "N/A (single mode)",
        kimi_api_key_configured=bool(settings.kimi_api_key),
        gemini_api_key_configured=bool(settings.gemini_api_key),
        effective_mode=effective_mode,
    )


@router.put("")
async def update_llm_config(update: LLMConfigUpdate):
    """Update LLM chain mode (single/chain)."""
    if update.mode and update.mode in ("single", "chain"):
        settings.llm_mode = update.mode
        effective = update.mode
        if update.mode == "chain" and not settings.kimi_api_key:
            effective = "single"
            logger.warning("Chain mode requested but no Kimi API key configured — will use single mode")

        logger.info(f"LLM mode updated to '{update.mode}' (effective: '{effective}')")
        return {
            "status": "ok",
            "mode": update.mode,
            "effective_mode": effective,
            "message": f"LLM mode set to '{update.mode}'"
            + (" (degraded to single — no Kimi API key)" if effective != update.mode else ""),
        }
    return {"status": "error", "message": "Invalid mode. Use 'single' or 'chain'."}


@router.get("/usage", response_model=LLMUsageResponse)
async def get_llm_usage():
    """Return daily LLM usage stats including review verdicts."""
    gemma_budget = get_daily_budget_status()
    review = get_review_stats()

    # Get Kimi stats
    kimi_calls = 0
    kimi_model = settings.kimi_model
    try:
        from kimi_engine import get_kimi_daily_usage
        kimi_usage = get_kimi_daily_usage()
        kimi_calls = kimi_usage.get("calls_today", 0)
        kimi_model = kimi_usage.get("model", settings.kimi_model)
    except ImportError:
        pass

    return LLMUsageResponse(
        gemma_calls_today=gemma_budget.get("calls_today", 0),
        gemma_daily_limit=gemma_budget.get("daily_limit", 1500),
        kimi_calls_today=kimi_calls,
        kimi_model=kimi_model,
        review_stats=review,
    )
