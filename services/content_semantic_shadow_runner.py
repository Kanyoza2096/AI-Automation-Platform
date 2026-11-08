"""Staging-only, no-side-effect runner for direct semantic shadow evaluation.

Evidence is retrieved through the existing KnowledgeBase/KnowledgeRepository
using the exact brand and workspace scope. The caller must supply a repository
backed by the staging service client; evidence is never accepted as arbitrary
caller-provided text.
"""
from __future__ import annotations

import os
from typing import Any

from repositories.knowledge_repository import KnowledgeRepository
from services.knowledge_base import KnowledgeBase
from services.content_semantic_reviewer import ContentSemanticReviewer

STAGING_WORKSPACE_ID = "kdo-engineering-staging"
MAX_CAPTIONS_PER_RUN = 5
MAX_EVIDENCE_PER_CAPTION = 3


class ShadowEvaluationRefused(RuntimeError):
    """Raised when the staging-only evaluation boundary is not satisfied."""


def _assert_staging_boundary(brand: Any) -> tuple[str, str]:
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
    brand_id = getattr(brand, "id", None)
    if workspace_id != STAGING_WORKSPACE_ID:
        raise ShadowEvaluationRefused(
            "brand workspace does not match the canonical KDO staging workspace"
        )
    if not isinstance(brand_id, str) or not brand_id.strip():
        raise ShadowEvaluationRefused("a persisted brand ID is required")
    return workspace_id, brand_id


async def resolve_staging_evidence(
    *,
    query: str,
    brand: Any,
    repository: KnowledgeRepository,
    top_k: int = MAX_EVIDENCE_PER_CAPTION,
) -> list[dict[str, str]]:
    """Retrieve evidence from the exact brand/workspace partition and verify provenance."""
    workspace_id, brand_id = _assert_staging_boundary(brand)
    if not isinstance(query, str) or not query.strip():
        raise ShadowEvaluationRefused("an evidence retrieval query is required")
    if not isinstance(repository, KnowledgeRepository):
        raise ShadowEvaluationRefused("a KnowledgeRepository backed by staging is required")
    if not 1 <= top_k <= MAX_EVIDENCE_PER_CAPTION:
        raise ShadowEvaluationRefused("evidence retrieval is limited to 1–3 records")

    knowledge_base = KnowledgeBase(
        brand_id=brand_id,
        workspace_id=workspace_id,
        repository=repository,
    )
    await knowledge_base.initialize()
    matches = await knowledge_base.search(query, top_k=top_k)

    evidence: list[dict[str, str]] = []
    for entry, _score in matches:
        # Defense in depth: do not trust repository filtering alone.
        if (
            getattr(entry, "workspace_id", None) != workspace_id
            or getattr(entry, "brand_id", None) != brand_id
            or not isinstance(getattr(entry, "id", None), str)
            or not isinstance(getattr(entry, "content", None), str)
            or not entry.id.strip()
            or not entry.content.strip()
        ):
            raise ShadowEvaluationRefused(
                "retrieved evidence failed brand/workspace provenance validation"
            )
        evidence.append({"source_id": entry.id, "text": entry.content[:1200]})

    if not evidence:
        raise ShadowEvaluationRefused(
            "no matching knowledge evidence exists for this staging brand"
        )
    return evidence


async def evaluate_caption(
    *,
    caption: str,
    topic: str,
    brand: Any,
    repository: KnowledgeRepository,
    reviewer: ContentSemanticReviewer | None = None,
) -> dict[str, Any]:
    """Retrieve scoped evidence and evaluate one caption; never publish or persist."""
    _assert_staging_boundary(brand)
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 5000:
        raise ShadowEvaluationRefused("caption must contain 1–5000 characters")
    evidence = await resolve_staging_evidence(
        query=f"{topic}\n{caption}",
        brand=brand,
        repository=repository,
    )

    semantic_reviewer = reviewer or ContentSemanticReviewer()
    result = await semantic_reviewer.review(
        caption=caption,
        topic=topic,
        evidence=evidence,
        brand=brand,
        platform="facebook",
    )
    return {
        "evaluation_type": "staging_semantic_shadow",
        "workspace_id": STAGING_WORKSPACE_ID,
        "brand_id": brand.id,
        "evidence_source_ids": [item["source_id"] for item in evidence],
        "published": False,
        "persisted": False,
        "result": result,
    }


async def evaluate_batch(
    *,
    cases: list[dict[str, Any]],
    brand: Any,
    repository: KnowledgeRepository,
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
            brand=brand,
            repository=repository,
            reviewer=reviewer,
        ))
    return results


__all__ = [
    "STAGING_WORKSPACE_ID",
    "MAX_CAPTIONS_PER_RUN",
    "MAX_EVIDENCE_PER_CAPTION",
    "ShadowEvaluationRefused",
    "resolve_staging_evidence",
    "evaluate_caption",
    "evaluate_batch",
]
