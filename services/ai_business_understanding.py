"""Bounded AI-assisted business understanding.

The model is an interpreter, not an actor. It may extract a small allow-listed
business signal with evidence and confidence, but it cannot execute tools,
choose arbitrary actions, or mutate application state.

If no AI endpoint is configured, callers receive a deterministic fallback so
production behavior remains explicit and auditable rather than silently failing.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

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
        "provider": "openai_compatible",
        "model": os.getenv("KDO_AI_MODEL", "gpt-4.1-mini"),
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
    endpoint = os.getenv("KDO_AI_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("KDO_AI_API_KEY", "").strip()
    if not endpoint or not api_key or not text:
        return base

    url = endpoint if endpoint.endswith("/chat/completions") else endpoint + "/chat/completions"
    payload = {
        "model": os.getenv("KDO_AI_MODEL", "gpt-4.1-mini"),
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"customer_text": text[:4000]}, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
            data = response.json()
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
        if isinstance(content, list):
            content = "".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
        if not isinstance(content, str):
            return base
        parsed = json.loads(content)
        cleaned = _clean(parsed, text)
        return cleaned or base
    except Exception:
        return {**base, "provider": "deterministic_fallback", "rationale": "AI provider unavailable; deterministic interpretation retained."}
