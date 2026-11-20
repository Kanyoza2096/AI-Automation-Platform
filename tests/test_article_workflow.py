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


def test_article_validation_bypasses_citation_and_source_checkers():
    class Reviewer:
        async def review(self, **kwargs):
            raise AssertionError("article reviewer must not be invoked")
    context = _article_context()
    context["article"]["citations"] = ["unknown-source"]
    result = asyncio.run(ValidateArticleStep(Reviewer()).async_run(context))
    assert result["approved"] is True
    assert result["review_status"] == "disabled"
    assert context["article_review"]["status"] == "disabled"

def test_article_validation_bypasses_semantic_review():
    class Reviewer:
        async def review(self, **kwargs):
            raise AssertionError("article semantic reviewer must not be invoked")
    result = asyncio.run(ValidateArticleStep(Reviewer()).async_run(_article_context()))
    assert result["approved"] is True
    assert result["review_status"] == "disabled"

def test_article_validation_does_not_invoke_reviewer():
    class Reviewer:
        async def review(self, **kwargs):
            raise AssertionError("article reviewer must not be invoked")
    context = _article_context()
    result = asyncio.run(ValidateArticleStep(Reviewer()).async_run(context))
    assert result["approved"] is True
    assert context["article_review"]["status"] == "disabled"

def test_article_publish_step_does_not_require_review_metadata():
    from pathlib import Path
    source = Path("workflows/article_workflow.py").read_text(encoding="utf-8")
    publish = source[source.index("class PublishArticleStep"):source.index("class PreviewArticleStep")]
    assert "article_publish_quality_gate" not in publish
    assert "article_publish_review_missing" not in publish

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


def test_article_preview_requires_only_generated_article():
    from workflows.article_workflow import PreviewArticleStep
    context = _article_context()
    result = asyncio.run(PreviewArticleStep().async_run(context))
    assert result["mode"] == "preview_only"
    assert result["publishable"] is False
    assert result["title"] == context["article"]["title"]
    assert "publish_result" not in context

def test_article_preview_does_not_require_semantic_review():
    from workflows.article_workflow import PreviewArticleStep
    context = _article_context()
    result = asyncio.run(PreviewArticleStep().async_run(context))
    assert result["publishable"] is False

