"""Jev SystemOne bounded-decision adapter (v2 Phase 3).
The only AI in the system. It answers choice/score/noul questions, nothing else.
Wire: POST https://jevmodel.org/v1/systemone, Bearer $JEVMODEL_API_KEY,
body {model, state<=8000 chars, questions<=8}. Output tokens are free;
only usage.input_tokens costs money ($0.042 / 1M input)."""
import asyncio
import os
import uuid
import logging
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)

JEV_ENDPOINT = "https://jevmodel.org/v1/systemone"
DEFAULT_MODEL = "jev-latest"   # pin to a fixed version once thresholds are tuned
MAX_QUESTIONS_PER_REQUEST = 8
MAX_STATE_CHARS = 8000
PRICE_PER_1M_INPUT = 0.042
HARD_MONTHLY_CAP_USD = 2.0

class JevAdapter:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 30.0,
                 max_retries: int = 3, backoff_start: float = 1.0):
        if api_key is not None:
            self.api_key = api_key
        else:
            from config import settings
            self.api_key = os.environ.get("JEVMODEL_API_KEY") or getattr(settings, "jevmodel_api_key", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff_start
        self.last_tokens_remaining: Optional[int] = None
        self.total_input_tokens = 0
        self.calls = 0
        self.failures = 0
        self.disabled_reason: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    @property
    def jev_online(self) -> bool:
        return self.is_configured and self.disabled_reason is None

    def meter(self) -> dict:
        return {
            "jev_online": self.jev_online,
            "configured": self.is_configured,
            "disabled_reason": self.disabled_reason,
            "calls": self.calls,
            "failures": self.failures,
            "total_input_tokens": self.total_input_tokens,
            "last_tokens_remaining": self.last_tokens_remaining,
            "estimated_spend_usd": round(self.total_input_tokens / 1e6 * PRICE_PER_1M_INPUT, 6),
            "monthly_cap_usd": HARD_MONTHLY_CAP_USD,
        }

    async def query(self, state: str, questions: Dict[str, Dict[str, Any]],
                    model: str = DEFAULT_MODEL) -> Optional[Dict[str, Any]]:
        """Answers dict on success. None when Jev is down/disabled — callers
        MUST treat None as 'skip the AI step' (fail-closed)."""
        if not self.is_configured:
            self.disabled_reason = self.disabled_reason or "no_api_key"
            return None
        if self.disabled_reason:
            return None
        if not questions or len(questions) > MAX_QUESTIONS_PER_REQUEST:
            raise ValueError(f"questions must be 1..{MAX_QUESTIONS_PER_REQUEST}")
        payload = {"model": model, "state": state[:MAX_STATE_CHARS], "questions": questions}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        }
        backoff = self.backoff
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = await client.post(JEV_ENDPOINT, json=payload, headers=headers)
                except httpx.RequestError as exc:
                    self.failures += 1
                    if attempt == self.max_retries:
                        logger.warning(f"Jev unreachable: {exc}")
                        return None
                    await asyncio.sleep(backoff); backoff *= 2; continue
                rem = resp.headers.get("X-Tokens-Remaining")
                if rem is not None:
                    try:
                        self.last_tokens_remaining = int(rem)
                    except ValueError:
                        pass
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage") or {}
                    self.total_input_tokens += int(usage.get("input_tokens", 0) or 0)
                    self.calls += 1
                    return data.get("answers") or {}
                if resp.status_code in (429, 502):
                    self.failures += 1
                    if attempt == self.max_retries:
                        logger.warning(f"Jev {resp.status_code} after {attempt} attempts")
                        return None
                    await asyncio.sleep(backoff); backoff *= 2; continue  # same Idempotency-Key
                if resp.status_code == 402:
                    self.disabled_reason = "quota_exhausted_402"
                    logger.warning("Jev quota exhausted (402) — code-only mode")
                    return None
                if resp.status_code == 401:
                    self.disabled_reason = "bad_key_401"
                    logger.error("Jev key rejected (401) — code-only mode")
                    return None
                self.failures += 1
                logger.warning(f"Jev unexpected {resp.status_code}: {resp.text[:150]}")
                return None
        return None
