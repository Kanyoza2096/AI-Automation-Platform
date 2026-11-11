"""Bounded AI-assisted business understanding.

The model is an interpreter, not an actor. It may extract a small allow-listed
business signal with evidence and confidence, but it cannot execute tools,
choose arbitrary actions, or mutate application state.

If the internal LLM gateway is unavailable, callers receive a deterministic
fallback so production behavior remains explicit and auditable rather than
silently failing.

V1.1 (2026-10-27) — INTERNAL GATEWAY EXTRACTION:

  `understand_customer_message` previously reached an external
  OpenAI-compatible endpoint gated on KDO_AI_BASE_URL and KDO_AI_API_KEY.
  Neither env var was ever set in production, so every call returned the
  deterministic fallback (intent="general_inquiry", confidence=0.0) —
  meaning intent-based rules never fired.

  Now: calls the platform's internal LLM gateway directly via
  get_llm_gateway().complete_async(capability="structured_json"). Same
  gateway the rest of the platform uses. No external HTTP, no env vars.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("kanyoza.services.ai_business_understanding")

ALLOWED_INTENTS = {
    "purchase_interest", "price_request", "meeting_request", "objection",
    "opt_out", "general_inquiry",
}

SYSTEM_PROMPT = """You are KDO Business Understanding, a bounded classifier.
Return JSON only with keys: intent, confidence, evidence, rationale.
intent MUST be one of: purchase_interest, price_request, meeting_request,
objection, opt_out, general_inquiry.
confidence MUST be a number from 0 to 1.
evidence MUST be an array of short exact excerpts copied from the customer text.
rationale MUST be a short factual explanation.
Never invent facts. Do not recommend actions. Do not contact anyone. Do not call tools.
If ambiguous, use general_inquiry and a low confidence."""


def _fallback(text: str, deterministic: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "deterministic_fallback",
        "model": "rules-v2",
        "intent": deterministic.get("intent", "general_inquiry"),
        "confidence": float(deterministic.get("confidence", 0.0) or 0.0),
        "evidence": [],
        "rationale": "No AI provider configured; bounded deterministic interpretation used.",
        "ai_used": False,
        "schema_version": "1.0",
    }


def _clean(result: Any, text: str) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    intent = str(result.get("intent") or "").strip()
    if intent not in ALLOWED_INTENTS:
        return None
    try:
        confidence = max(0.0, min(1.0, float(result.get("confidence"))))
    except (TypeError, ValueError):
        return None
    evidence = result.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list):
        evidence = []
    normalized_text = text.lower()
    safe_evidence = []
    for item in evidence[:5]:
        item = str(item).strip()
        if item and item.lower() in normalized_text:
            safe_evidence.append(item[:240])
    return {
        "provider": "internal_gateway",
        "model": "gemini-2.5-flash",
        "intent": intent,
        "confidence": round(confidence, 3),
        "evidence": safe_evidence,
        "rationale": str(result.get("rationale") or "")[:500],
        "ai_used": True,
        "schema_version": "1.0",
    }


async def understand_customer_message(text: str, *, deterministic: dict[str, Any] | None = None) -> dict[str, Any]:
    text = str(text or "").strip()
    deterministic = deterministic or {"intent": "general_inquiry", "confidence": 0.0}
    base = _fallback(text, deterministic)

    if not text:
        return base

    try:
        from ai.gateway import get_llm_gateway
    except Exception as exc:
        logger.warning(
            "understand_customer_message: gateway import failed (%s) — deterministic fallback",
            exc,
        )
        return {
            **base,
            "rationale": "AI gateway unavailable; deterministic interpretation retained.",
        }

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Customer text:\n{text[:4000]}\n\n"
        "Return a JSON object with keys: intent, confidence, evidence, rationale."
    )

    try:
        result = await get_llm_gateway().complete_async(
            prompt,
            capability="structured_json",
            temperature=0,
            max_tokens=500,
            caller="ai_business_understanding",
        )
    except Exception as exc:
        logger.warning(
            "understand_customer_message: gateway call raised (%s) — deterministic fallback",
            exc,
        )
        return {
            **base,
            "rationale": "AI provider unavailable; deterministic interpretation retained.",
        }

    if not result.ok or not result.text:
        logger.warning(
            "understand_customer_message: gateway declined (reason=%s mode=%s) "
            "— deterministic fallback",
            result.reason,
            getattr(result.mode, "value", result.mode),
        )
        return {
            **base,
            "provider": "deterministic_fallback",
            "rationale": f"AI provider unavailable ({result.reason}); deterministic interpretation retained.",
        }

    try:
        parsed = json.loads(result.text)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "understand_customer_message: gateway returned unparseable JSON (%s) "
            "— deterministic fallback",
            exc,
        )
        return {
            **base,
            "rationale": "AI response was not valid JSON; deterministic interpretation retained.",
        }

    cleaned = _clean(parsed, text)
    if cleaned is None:
        logger.warning(
            "understand_customer_message: LLM payload failed validation "
            "(intent missing/unknown or confidence invalid) — deterministic fallback",
        )
        return base

    logger.info(
        "understand_customer_message: LLM classification ok intent=%s "
        "confidence=%.3f provider=%s model=%s latency_ms=%.0f",
        cleaned["intent"], cleaned["confidence"],
        result.provider, result.model, result.latency_ms,
    )
    return cleaned
