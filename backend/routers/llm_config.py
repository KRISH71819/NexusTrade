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
    groq_api_key_configured: bool
    gemini_api_key_configured: bool
    effective_mode: str  # what's actually running (may differ from mode if no groq key)


class LLMConfigUpdate(BaseModel):
    mode: Optional[str] = None  # "single" or "chain"


class LLMUsageResponse(BaseModel):
    gemma_calls_today: int
    gemma_daily_limit: int
    groq_compound_calls_today: int
    groq_compound_daily_limit: int
    groq_llama_calls_today: int
    groq_llama_daily_limit: int
    groq_primary_model: str
    groq_fallback_model: str
    review_stats: dict


@router.get("", response_model=LLMConfigResponse)
async def get_llm_config():
    """Return current LLM chain configuration."""
    effective_mode = settings.llm_mode
    if effective_mode == "chain" and not settings.groq_api_key:
        effective_mode = "single"  # auto-degraded

    analyst_label = (
        f"Groq/{settings.groq_compound_model} → {settings.groq_fallback_model}"
        if effective_mode == "chain"
        else "Gemma 4 (31B)"
    )

    return LLMConfigResponse(
        mode=settings.llm_mode,
        analyst_model=settings.groq_compound_model if effective_mode == "chain" else settings.gemini_model,
        analyst_model_label=analyst_label,
        reviewer_model=settings.gemini_model if effective_mode == "chain" else "N/A",
        reviewer_model_label="Gemma 4 (31B)" if effective_mode == "chain" else "N/A (single mode)",
        groq_api_key_configured=bool(settings.groq_api_key),
        gemini_api_key_configured=bool(settings.gemini_api_key),
        effective_mode=effective_mode,
    )


@router.put("")
async def update_llm_config(update: LLMConfigUpdate):
    """Update LLM chain mode (single/chain)."""
    if update.mode and update.mode in ("single", "chain"):
        settings.llm_mode = update.mode
        effective = update.mode
        if update.mode == "chain" and not settings.groq_api_key:
            effective = "single"
            logger.warning("Chain mode requested but no Groq API key configured — will use single mode")

        logger.info(f"LLM mode updated to '{update.mode}' (effective: '{effective}')")
        return {
            "status": "ok",
            "mode": update.mode,
            "effective_mode": effective,
            "message": f"LLM mode set to '{update.mode}'"
            + (" (degraded to single — no Groq API key)" if effective != update.mode else ""),
        }
    return {"status": "error", "message": "Invalid mode. Use 'single' or 'chain'."}


@router.get("/usage", response_model=LLMUsageResponse)
async def get_llm_usage():
    """Return daily LLM usage stats including review verdicts."""
    gemma_budget = get_daily_budget_status()
    review = get_review_stats()

    # Get Groq stats
    groq_compound_calls = 0
    groq_llama_calls = 0
    groq_compound_limit = 250
    groq_llama_limit = 1000
    try:
        from groq_engine import get_groq_daily_usage
        groq_usage = get_groq_daily_usage()
        groq_compound_calls = groq_usage.get("compound_calls_today", 0)
        groq_llama_calls = groq_usage.get("llama_calls_today", 0)
        groq_compound_limit = groq_usage.get("compound_daily_limit", 250)
        groq_llama_limit = groq_usage.get("llama_daily_limit", 1000)
    except ImportError:
        pass

    return LLMUsageResponse(
        gemma_calls_today=gemma_budget.get("calls_today", 0),
        gemma_daily_limit=gemma_budget.get("daily_limit", 1500),
        groq_compound_calls_today=groq_compound_calls,
        groq_compound_daily_limit=groq_compound_limit,
        groq_llama_calls_today=groq_llama_calls,
        groq_llama_daily_limit=groq_llama_limit,
        groq_primary_model=settings.groq_compound_model,
        groq_fallback_model=settings.groq_fallback_model,
        review_stats=review,
    )
