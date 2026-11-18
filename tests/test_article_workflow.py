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
            return {
                "approved": False,
                "status": "needs_review",
                "decision": "needs_review",
                "reason": "claim support is uncertain",
                "rationale": "Two claims need stronger evidence.",
                "evidence_coverage": {
                    "assessed": 4, "supported": 2, "unsupported": 1,
                    "uncertain": 1, "supported_rate": 0.5,
                },
                "unsupported_claims": [
                    {"claim": "Claim X", "support_status": "unsupported", "source_ids": [], "rationale": "No evidence"}
                ],
                "scores": {
                    "claim_coverage": 0.95, "brand_relevance": 0.8,
                    "originality": 0.6, "audience_value": 0.7, "genericity": 0.2,
                },
                "request_id": "req-review-123",
                "provider": "cloudflare",
                "model": "llama-review",
            }

    with pytest.raises(StepFailure) as exc:
        asyncio.run(ValidateArticleStep(Reviewer()).async_run(_article_context()))
    assert exc.value.code == "article_semantic_review_not_approved"
    diagnostics = exc.value.detail["review_diagnostics"]
    assert diagnostics["decision"] == "needs_review"
    assert diagnostics["evidence_coverage"]["supported_rate"] == 0.5
    assert diagnostics["unsupported_claims"][0]["claim"] == "Claim X"
    assert diagnostics["scores"]["brand_relevance"] == 0.8
    assert diagnostics["model"] == "llama-review"
    assert diagnostics["request_id"] == "req-review-123"


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
    assert _brand_publish_platform(brand) == "website"


def test_scheduler_preserves_social_platform_default():
    brand = SimpleNamespace(platform=None, metadata={})
    assert _brand_publish_platform(brand) == "facebook"



def test_article_workflow_dry_run_excludes_publish_step():
    from workflows.article_workflow import ArticlePublishWorkflow, PreviewArticleStep

    workflow = ArticlePublishWorkflow(
        brand=SimpleNamespace(id="brand-1", name="GrowthBridge", workspace_id="ws-1"),
        dry_run=True,
    )
    step_names = [step.name for step in workflow._steps]
    assert workflow.name == "article_preview"
    assert step_names == [
        "select_topic", "knowledge_context", "generate_article",
        "validate_article", "article_preview",
    ]
    assert not any(isinstance(step, PublishArticleStep) for step in workflow._steps)
    assert isinstance(workflow._steps[-1], PreviewArticleStep)


def test_article_preview_requires_validation_and_independent_review():
    from workflows.article_workflow import PreviewArticleStep

    context = _article_context()
    context["article_validation"] = {"approved": True, "citations": ["kb-1"]}
    context["article_review"] = {
        "approved": True, "status": "approved", "reason": "all claims supported",
    }
    result = asyncio.run(PreviewArticleStep().async_run(context))
    assert result["mode"] == "preview_only"
    assert result["publishable"] is False
    assert result["title"] == context["article"]["title"]
    assert result["citations"] == ["kb-1"]
    assert "publish_result" not in context
    assert "publish_results" not in context


def test_article_preview_fails_closed_without_semantic_review():
    from workflows.article_workflow import PreviewArticleStep

    context = _article_context()
    context["article_validation"] = {"approved": True, "citations": ["kb-1"]}
    with pytest.raises(StepFailure) as exc:
        asyncio.run(PreviewArticleStep().async_run(context))
    assert exc.value.code == "article_preview_review_missing"
