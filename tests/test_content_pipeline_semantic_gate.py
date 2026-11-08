from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.errors import AIValidationError


def _approved_review():
    return {
        "approved": True,
        "status": "approved",
        "reason": "evidence and editorial quality passed",
        "scores": {
            "claim_coverage": 1.0,
            "brand_relevance": 0.9,
            "originality": 0.8,
            "audience_value": 0.8,
            "genericity": 0.2,
        },
        "supporting_source_ids": ["kb-1"],
        "request_id": "review-1",
    }


def test_knowledge_step_preserves_traceable_evidence_for_semantic_review(monkeypatch):
    import services.pipeline.pipeline as pipeline_module

    entry = SimpleNamespace(
        id="entry-1",
        content="Canary deployments expose a limited share of traffic first.",
        metadata={"source_id": "kb-canary-1", "verified": True},
    )

    class FakeKnowledgeBase:
        def __init__(self, **kwargs):
            assert kwargs["brand_id"] == "brand-1"
            assert kwargs["workspace_id"] == "workspace-1"

        def search(self, topic, top_k):
            assert topic == "canary deployments"
            assert top_k == 3
            return [(entry, 0.94)]

    monkeypatch.setattr(pipeline_module, "KnowledgeBase", FakeKnowledgeBase)
    step = pipeline_module.KnowledgeStep(
        brand=SimpleNamespace(id="brand-1", workspace_id="workspace-1"),
        repo=MagicMock(),
    )
    context = {"topic": "canary deployments"}

    result = asyncio.run(step.run_async(context))

    assert result == context["knowledge_evidence"]
    assert context["knowledge_evidence"][0]["source_id"] == "kb-canary-1"
    assert context["knowledge_evidence"][0]["text"] == entry.content
    assert context["knowledge_evidence"][0]["metadata"]["verified"] is True


def test_semantic_review_step_records_explicit_approval():
    from services.pipeline.pipeline import SemanticReviewStep

    reviewer = MagicMock()
    reviewer.review = AsyncMock(return_value=_approved_review())
    brand = SimpleNamespace(id="brand-1", workspace_id="workspace-1")
    evidence = [{"source_id": "kb-1", "text": "Grounded source", "metadata": {}}]
    context = {
        "brand": brand,
        "caption": "A technically grounded post.",
        "topic": "canary deployments",
        "knowledge_evidence": evidence,
        "platform": "facebook",
    }

    result = asyncio.run(
        SemanticReviewStep(semantic_reviewer=reviewer).run_async(context)
    )

    reviewer.review.assert_awaited_once_with(
        caption="A technically grounded post.",
        topic="canary deployments",
        evidence=evidence,
        brand=brand,
        platform="facebook",
    )
    assert result["approved"] is True
    assert context["semantic_review"]["status"] == "approved"


@pytest.mark.parametrize(
    "review",
    [
        {"approved": False, "status": "rejected", "reason": "unsupported claim"},
        {"approved": False, "status": "needs_review", "reason": "uncertain claim"},
        None,
        {"approved": True, "status": "needs_review"},
    ],
)
def test_semantic_review_step_fails_closed_for_non_approval(review):
    from services.pipeline.pipeline import SemanticReviewStep

    reviewer = MagicMock()
    reviewer.review = AsyncMock(return_value=review)
    context = {
        "brand": SimpleNamespace(id="brand-1", workspace_id="workspace-1"),
        "caption": "A post.",
        "topic": "topic",
        "knowledge_evidence": [{"source_id": "kb-1", "text": "source"}],
    }

    with pytest.raises(AIValidationError, match="Independent semantic review blocked"):
        asyncio.run(SemanticReviewStep(semantic_reviewer=reviewer).run_async(context))


@pytest.mark.asyncio
async def test_publisher_refuses_to_publish_without_semantic_approval():
    from services.pipeline.pipeline import PublisherStep

    brand = SimpleNamespace(id="brand-1", workspace_id="workspace-1", name="Test")
    step = PublisherStep(brand=brand, container=MagicMock())

    with patch(
        "domain.publishing.canonical_pipeline.run_canonical_publish",
        new_callable=AsyncMock,
    ) as publish:
        with pytest.raises(AIValidationError, match="semantic approval is missing"):
            await step.run_async({"caption": "must not publish"})

    publish.assert_not_awaited()
