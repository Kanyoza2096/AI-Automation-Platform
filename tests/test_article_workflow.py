import asyncio
from types import SimpleNamespace

import pytest

from workflows.base import StepFailure
from workflows.article_workflow import ValidateArticleStep, PublishArticleStep
from scheduler.brand_scheduler import _brand_publish_platform


def _article_context():
    body = ("A useful evidence-grounded explanation of the subject, with practical "
            "considerations and clear distinctions between established facts and "
            "recommendations. " * 18).strip()
    return {
        "brand": SimpleNamespace(id="brand-1", name="GrowthBridge", workspace_id="ws-1"),
        "workspace_id": "ws-1",
        "topic": "Responsible digital growth",
        "article": {
            "title": "A Practical Guide to Responsible Digital Growth",
            "excerpt": "A grounded guide to building sustainable digital systems and services.",
            "body": body,
            "citations": ["kb-1"],
        },
        "knowledge_evidence": [{
            "source_id": "kb-1",
            "text": "Sustainable digital systems require clear goals and ongoing evaluation.",
            "metadata": {"verified": True},
        }],
    }


def test_article_validation_requires_known_citations_before_review():
    class Reviewer:
        called = False
        async def review(self, **kwargs):
            self.called = True
            return {"approved": True, "status": "approved", "reason": "ok"}

    reviewer = Reviewer()
    context = _article_context()
    context["article"]["citations"] = ["not-in-evidence"]
    with pytest.raises(StepFailure) as exc:
        asyncio.run(ValidateArticleStep(reviewer).async_run(context))
    assert exc.value.code == "article_citations_invalid"
    assert reviewer.called is False


def test_article_validation_requires_independent_full_text_approval():
    class Reviewer:
        async def review(self, **kwargs):
            assert len(kwargs["body"]) > 1200
            return {"approved": False, "status": "needs_review", "reason": "claim support is uncertain"}

    with pytest.raises(StepFailure) as exc:
        asyncio.run(ValidateArticleStep(Reviewer()).async_run(_article_context()))
    assert exc.value.code == "article_semantic_review_not_approved"


def test_article_validation_passes_only_after_full_article_review():
    class Reviewer:
        async def review(self, **kwargs):
            assert kwargs["title"].startswith("A Practical Guide")
            assert len(kwargs["body"]) > 1200
            return {"approved": True, "status": "approved", "reason": "all reviewed claims supported"}

    context = _article_context()
    result = asyncio.run(ValidateArticleStep(Reviewer()).async_run(context))
    assert result["approved"] is True
    assert context["article_review"]["status"] == "approved"


def test_article_publish_cannot_run_without_quality_gates():
    step = PublishArticleStep()
    with pytest.raises(StepFailure) as exc:
        asyncio.run(step.async_run({
            "brand": SimpleNamespace(id="brand-1", workspace_id="ws-1"),
            "workspace_id": "ws-1",
            "platform": "website",
            "article": {"title": "A title", "body": "An article body"},
        }))
    assert exc.value.code == "article_publish_quality_gate"


def test_scheduler_routes_metadata_website_brand_to_article_platform():
    brand = SimpleNamespace(platform=None, metadata={"primary_platform": "website"})
    assert _brand_content_platform(brand) == "website"


def test_scheduler_preserves_social_platform_default():
    brand = SimpleNamespace(platform=None, metadata={})
    assert _brand_content_platform(brand) == "facebook"
