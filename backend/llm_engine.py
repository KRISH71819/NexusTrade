"""
LLM Engine — Gemini sentiment analysis on news headlines.
"""

import logging
import asyncio
from typing import List
from google import genai
from pydantic import BaseModel
from config import settings

logger = logging.getLogger(__name__)

class SentimentResult(BaseModel):
    sentiment_score: float
    explanation: str

# Lazy init client
_client = None

def _get_client():
    global _client
    if _client is None and settings.gemini_api_key:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client

def _analyze_sync(ticker: str, headlines: List[str]) -> dict:
    client = _get_client()
    if not client:
        logger.warning("Gemini API key not configured. Returning neutral sentiment.")
        return {"sentiment_score": 0.0, "explanation": "Gemini API key not configured."}

    if not headlines:
        return {"sentiment_score": 0.0, "explanation": "No news available to analyze."}

    prompt = f"Analyze the sentiment for stock {ticker} based on these recent news headlines:\n"
    for h in headlines:
        prompt += f"- {h}\n"
    
    prompt += "\nReturn a sentiment score between -1.0 (very bearish) and 1.0 (very bullish), and a 2-sentence explanation of the reasoning."

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SentimentResult,
                temperature=0.2,
            ),
        )
        
        # Pydantic parsing of the response text
        result = SentimentResult.model_validate_json(response.text)
        
        return {
            "sentiment_score": result.sentiment_score,
            "explanation": result.explanation,
        }
    except Exception as e:
        logger.error(f"Gemini API error for {ticker}: {e}")
        return {
            "sentiment_score": 0.0,
            "explanation": f"Failed to analyze sentiment: {e}",
        }


async def analyze_sentiment(ticker: str, headlines: List[str]) -> dict:
    """
    Feed headlines to Gemini and get a structured sentiment analysis.

    Returns:
        {
            "sentiment_score": float,   # -1.0 to 1.0
            "explanation": str,         # 2-sentence reasoning
        }
    """
    logger.info(f"Analyzing LLM sentiment for {ticker}")
    return await asyncio.to_thread(_analyze_sync, ticker, headlines)
