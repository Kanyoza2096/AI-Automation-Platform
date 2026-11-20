"""Dedicated long-form article workflow for website publishing."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from workflows.base import Step, StepFailure, Workflow
from workflows.post_workflow import KnowledgeContextStep, SelectTopicStep


class GenerateArticleStep(Step):
    name = "generate_article"

    async def async_run(self, context: dict[str, Any]) -> dict[str, Any]:
        brand = context.get("brand")
        workspace_id = str(context.get("workspace_id") or getattr(brand, "workspace_id", "") or "").strip()
        topic = str(context.get("topic") or "").strip()
        evidence = context.get("knowledge_evidence")
        if not workspace_id or not topic:
            raise StepFailure(
                "Article generation requires a tenant and topic",
                code="article_generation_prerequisites_missing",
            )
        metadata = getattr(brand, "metadata", None)
        metadata = metadata if isinstance(metadata, dict) else {}
        brand_context = {
            "name": str(getattr(brand, "name", "") or ""),
            "description": str(getattr(brand, "description", "") or metadata.get("description", "")),
            "industry": str(getattr(brand, "industry", "") or metadata.get("industry", "")),
            "audience": str(getattr(brand, "audience_description", "") or metadata.get("target_audience", "")),
            "voice": str(getattr(brand, "voice", "") or metadata.get("voice", "")),
        }
        source_material = [
            {"source_id": str(item.get("source_id")), "text": str(item.get("text") or "")[:2200]}
            for item in evidence[:6] if isinstance(item, dict) and item.get("source_id") and item.get("text")
        ]
        prompt = (
            "Create an original, useful long-form website article for this brand. "
            "Use only the supplied evidence for factual claims. Do not invent statistics, "
            "case studies, customer stories, quotations, citations, or claims about the "
            "brand. Clearly distinguish general explanation from evidence-backed facts. "
            "Write in the brand's voice for its stated audience. Avoid first-person "
            "experience unless explicitly present in evidence. Return a compelling, "
            "accurate title, concise excerpt, complete article body in clean Markdown, "
            "and source_ids used. Aim for 700-1100 words without padding. The article "
            "must have a clear introduction, descriptive section headings, practical "
            "detail, and a useful conclusion.\n"
            f"Brand: {brand_context}\nTopic: {topic}\n"
            f"Allowed citation source IDs (use exact strings only): {[item['source_id'] for item in source_material]}\n"
            "The citations array must contain only exact IDs from that allowed list. "
            "Never invent, rewrite, infer, or cite an ID not listed. If no supplied source "
            "supports the article, do not fabricate a citation.\n"
            f"Evidence: {source_material}"
        )
        try:
            from ai.gateway import get_llm_gateway
            result = await get_llm_gateway().complete_async_structured(
                prompt,
                schema_hint=(
                    "Return one JSON object with title (string), excerpt (string), "
                    "body (string, Markdown article), and citations (array of exact source_id strings)."
                ),
                workspace_id=workspace_id,
                temperature=0.35,
                max_tokens=4000,
                required_keys=["title", "excerpt", "body", "citations"],
                caller="content",
            )
        except Exception as exc:
            raise StepFailure(
                "Article generation service is unavailable",
                code="article_generation_unavailable",
                detail={"exception_type": type(exc).__name__},
            ) from exc
        article = getattr(result, "data", None)
        if not getattr(result, "ok", False) or not isinstance(article, dict):
            raise StepFailure(
                "Article generation returned no valid structured article",
                code="article_generation_invalid",
                detail={"reason": str(getattr(result, "reason", "invalid_result"))[:500]},
            )
        context["article"] = {
            "title": str(article.get("title") or "").strip(),
            "excerpt": str(article.get("excerpt") or "").strip(),
            "body": str(article.get("body") or "").strip(),
            "citations": article.get("citations"),
        }
        return {"title": context["article"]["title"], "body_chars": len(context["article"]["body"])}


class ValidateArticleStep(Step):
    name = "validate_article"

    def __init__(self, reviewer=None):
        self._reviewer = reviewer

    async def async_run(self, context: dict[str, Any]) -> dict[str, Any]:
        article = context.get("article")
        if not isinstance(article, dict):
            raise StepFailure("Generated article is missing", code="article_missing")
        # Operator disable: bypass article length, placeholder, citation/source,
        # and semantic-review checkers. Preserve only the existence of the
        # generated article; governed publication remains separate.
        context["article_review"] = {
            "status": "disabled",
            "approved": None,
            "reason": "article content review checkers disabled by operator",
        }
        context["article_validation"] = {
            "approved": True,
            "review_status": "disabled",
            "citations": article.get("citations") if isinstance(article.get("citations"), list) else [],
        }
        return context["article_validation"]


class PublishArticleStep(Step):
    name = "publish"

    def __init__(self, container=None):
        self._container = container

    async def async_run(self, context: dict[str, Any]) -> dict[str, Any]:
        article = context.get("article") or {}
        # Automated article quality, source, citation, and semantic-review
        # gates are disabled. Keep article presence and governed publish controls.
        if not isinstance(article, dict) or not str(article.get("body") or "").strip():
            raise StepFailure("Article body is missing", code="article_missing")
        brand = context.get("brand")
        workspace_id = str(context.get("workspace_id") or getattr(brand, "workspace_id", "") or "").strip()
        brand_id = str(context.get("brand_id") or getattr(brand, "id", "") or "").strip()
        if not workspace_id or not brand_id:
            raise StepFailure("Publishing requires a concrete brand and workspace", code="article_publish_scope_missing")
        from core.publish_authorization import issue_internal_publish_decision
        from services.publish_orchestration import execute_governed_publish
        platform = str(context.get("platform") or "website").lower()
        if platform != "website":
            raise StepFailure("Article workflow is restricted to website publishing", code="article_platform_invalid")
        source = str(context.get("source") or "scheduled")
        decision = issue_internal_publish_decision(
            workspace_id=workspace_id, source=source, risk_level=context.get("risk_level", "low"),
            brand_id=brand_id, platform="website",
        )
        job_id = context.get("job_id")
        scheduled_post_id = context.get("scheduled_post_id")
        result = await execute_governed_publish(
            container=self._container,
            workspace_id=workspace_id,
            brand_id=brand_id,
            platform="website",
            topic=article["title"],
            caption=article["body"],
            risk_level=context.get("risk_level", "low"),
            decision_id=decision.decision_id,
            approved=True,
            automation_job_id=job_id,
            source=source,
            scheduled_post_id=scheduled_post_id,
            correlation_id=context.get("correlation_id") or hashlib.sha256(
                f"{workspace_id}:{brand_id}:{job_id}:{article['title']}".encode()
            ).hexdigest(),
            idempotency_key=(
                f"scheduled:{scheduled_post_id}" if scheduled_post_id
                else f"automation-job:{job_id}" if job_id else None
            ),
        )
        output = result.get("output") if isinstance(result, dict) and isinstance(result.get("output"), dict) else {}
        status = str((result or {}).get("status") or "").lower()
        post_id = (
            output.get("platform_post_id") or output.get("post_id")
            or output.get("provider_object_id") or (result or {}).get("provider_object_id")
            or (result or {}).get("platform_post_id")
        )
        if status not in {"succeeded", "success", "ok", "published"} or not post_id:
            raise StepFailure(
                "Website publish was not confirmed with a provider receipt; reconciliation is required",
                code="article_publish_unconfirmed",
                detail={"status": status, "reconciliation_required": status in {"uncertain", "in_progress"}},
            )
        publish_result = {**output, "status": status, "post_id": post_id, "platform_post_id": post_id, "governed": True}
        context["publish_result"] = publish_result
        context["publish_results"] = {"website": publish_result}
        return publish_result


class PreviewArticleStep(Step):
    """Return a validated article artifact without invoking any publisher."""

    name = "article_preview"

    async def async_run(self, context: dict[str, Any]) -> dict[str, Any]:
        article = context.get("article")
        validation = context.get("article_validation")
        review = context.get("article_review")
        if not isinstance(article, dict):
            raise StepFailure("Article preview requires a generated article", code="article_preview_missing")
        evidence = context.get("knowledge_evidence") or []
        preview = {
            "mode": "preview_only",
            "publishable": False,
            "title": article["title"],
            "excerpt": article["excerpt"],
            "body": article["body"],
            "citations": list(article["citations"]),
            "evidence_sources": [
                {
                    "source_id": item.get("source_id"),
                    "title": (item.get("metadata") or {}).get("title"),
                }
                for item in evidence if isinstance(item, dict)
            ],
            "review": review,
        }
        context["article_preview"] = preview
        return preview


class ArticlePublishWorkflow(Workflow):
    name = "article_publish"

    def __init__(
        self, *, brand, ai_profile=None, knowledge_repo=None, container=None,
        category=None, dry_run=False,
    ):
        self._container = container
        steps = [
            SelectTopicStep(brand, category, knowledge_repo=knowledge_repo),
            KnowledgeContextStep(brand, knowledge_repo=knowledge_repo),
            GenerateArticleStep(),
            ValidateArticleStep(),
        ]
        if dry_run:
            self.name = "article_preview"
            steps.append(PreviewArticleStep())
        else:
            steps.append(PublishArticleStep(container=container))
        super().__init__(steps=steps)
