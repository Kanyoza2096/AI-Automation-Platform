"""Staging-only, no-side-effect runner for direct semantic shadow evaluation.

This module deliberately accepts an already-resolved Brand and knowledge
evidence. It does not retrieve data, invoke the post workflow, persist results,
render images, or publish to any platform. Callers must resolve both inputs
from the same explicitly approved staging tenant before invoking it.
"""
from __future__ import annotations

import os
from typing import Any

from services.content_semantic_reviewer import ContentSemanticReviewer

STAGING_WORKSPACE_ID = "kdo-engineering-staging"
MAX_CAPTIONS_PER_RUN = 5


class ShadowEvaluationRefused(RuntimeError):
    """Raised when the staging-only evaluation boundary is not satisfied."""


def _assert_staging_boundary(brand: Any) -> str:
    environment = (
        os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("KDO_ENVIRONMENT")
        or ""
    ).strip().lower()
    enabled = os.getenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", "").strip().lower()
    if environment != "staging":
        raise ShadowEvaluationRefused("semantic shadow evaluation is staging-only")
    if enabled not in {"1", "true", "yes"}:
        raise ShadowEvaluationRefused("semantic shadow evaluation feature flag is disabled")

    workspace_id = getattr(brand, "workspace_id", None)
    if workspace_id != STAGING_WORKSPACE_ID:
        raise ShadowEvaluationRefused(
            "brand workspace does not match the canonical KDO staging workspace"
        )
    return workspace_id


async def evaluate_caption(
    *,
    caption: str,
    topic: str,
    evidence: list[dict[str, Any]],
    brand: Any,
    reviewer: ContentSemanticReviewer | None = None,
) -> dict[str, Any]:
    """Evaluate one caption directly; return an ephemeral result, never publish."""
    _assert_staging_boundary(brand)
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 5000:
        raise ShadowEvaluationRefused("caption must contain 1–5000 characters")
    if not isinstance(evidence, list) or not evidence or len(evidence) > 3:
        raise ShadowEvaluationRefused("provide 1–3 actual staging knowledge evidence items")
    for item in evidence:
        if not isinstance(item, dict):
            raise ShadowEvaluationRefused("evidence item must be an object")
        if not str(item.get("source_id") or "").strip() or not str(item.get("text") or "").strip():
            raise ShadowEvaluationRefused("each evidence item needs a real source ID and text")

    semantic_reviewer = reviewer or ContentSemanticReviewer()
    result = await semantic_reviewer.review(
        caption=caption,
        topic=topic,
        evidence=evidence,
        brand=brand,
        platform="facebook",
    )
    # Keep output deliberately narrow and JSON-serializable. No DB writes.
    return {
        "evaluation_type": "staging_semantic_shadow",
        "workspace_id": STAGING_WORKSPACE_ID,
        "published": False,
        "persisted": False,
        "result": result,
    }


async def evaluate_batch(
    *,
    cases: list[dict[str, Any]],
    brand: Any,
    reviewer: ContentSemanticReviewer | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a small bounded batch sequentially to cap model spend."""
    _assert_staging_boundary(brand)
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CAPTIONS_PER_RUN:
        raise ShadowEvaluationRefused(
            f"batch must contain 1–{MAX_CAPTIONS_PER_RUN} captions"
        )
    results = []
    for case in cases:
        if not isinstance(case, dict):
            raise ShadowEvaluationRefused("each evaluation case must be an object")
        results.append(await evaluate_caption(
            caption=case.get("caption", ""),
            topic=case.get("topic", ""),
            evidence=case.get("evidence", []),
            brand=brand,
            reviewer=reviewer,
        ))
    return results


__all__ = [
    "STAGING_WORKSPACE_ID",
    "MAX_CAPTIONS_PER_RUN",
    "ShadowEvaluationRefused",
    "evaluate_caption",
    "evaluate_batch",
]
