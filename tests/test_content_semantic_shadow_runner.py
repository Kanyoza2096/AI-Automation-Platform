import asyncio
from types import SimpleNamespace

import pytest

from models.knowledge_entry import KnowledgeEntry
from repositories.knowledge_repository import KnowledgeRepository
from services.content_semantic_shadow_runner import (
    ShadowEvaluationRefused,
    evaluate_batch,
    evaluate_caption,
    resolve_staging_evidence,
)


def _brand(workspace_id="kdo-engineering-staging", brand_id="brand-staging-tech"):
    return SimpleNamespace(workspace_id=workspace_id, id=brand_id)


def _entry(
    entry_id="kb-staging-1",
    workspace_id="kdo-engineering-staging",
    brand_id="brand-staging-tech",
    content="Canary rollouts expose a limited share of traffic to a new version.",
):
    return KnowledgeEntry(
        id=entry_id,
        workspace_id=workspace_id,
        brand_id=brand_id,
        content=content,
    )


class FakeKnowledgeRepository(KnowledgeRepository):
    def __init__(self, entries):
        super().__init__()
        self.entries = entries
        self.calls = []
        self.supabase = SimpleNamespace(supabase_url="https://kkzshurcmcptleyqjgzo.supabase.co")

    def find_by_brand(self, brand_id, workspace_id):
        self.calls.append((brand_id, workspace_id))
        return [
            entry for entry in self.entries
            if entry.brand_id == brand_id and entry.workspace_id == workspace_id
        ]


class FakeReviewer:
    def __init__(self):
        self.calls = []

    async def review(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "approved": False,
            "status": "needs_review",
            "request_id": "test-request",
            "claims": [],
            "scores": {},
        }


def _enable_staging(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", "true")


def test_shadow_runner_retrieves_scoped_evidence_and_never_publishes(monkeypatch):
    _enable_staging(monkeypatch)
    repository = FakeKnowledgeRepository([
        _entry(),
        _entry("kb-other-brand", brand_id="brand-other"),
        _entry("kb-other-workspace", workspace_id="ws-e6eb6528d5b9"),
    ])
    reviewer = FakeReviewer()
    result = asyncio.run(evaluate_caption(
        caption="Canary rollouts limit initial exposure.",
        topic="canary rollout",
        brand=_brand(),
        repository=repository,
        reviewer=reviewer,
    ))

    assert repository.calls == [("brand-staging-tech", "kdo-engineering-staging")]
    assert len(reviewer.calls) == 1
    assert reviewer.calls[0]["evidence"] == [{
        "source_id": "kb-staging-1",
        "text": "Canary rollouts expose a limited share of traffic to a new version.",
    }]
    assert result["evaluation_type"] == "staging_semantic_shadow"
    assert result["workspace_id"] == "kdo-engineering-staging"
    assert result["brand_id"] == "brand-staging-tech"
    assert result["evidence_source_ids"] == ["kb-staging-1"]
    assert result["published"] is False
    assert result["persisted"] is False
    assert result["result"]["status"] == "needs_review"


def test_shadow_runner_refuses_non_staging_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", "true")
    reviewer = FakeReviewer()
    repository = FakeKnowledgeRepository([_entry()])

    with pytest.raises(ShadowEvaluationRefused, match="staging-only"):
        asyncio.run(evaluate_caption(
            caption="A test caption.", topic="topic", brand=_brand(),
            repository=repository, reviewer=reviewer,
        ))
    assert reviewer.calls == []
    assert repository.calls == []


def test_shadow_runner_requires_explicit_feature_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", raising=False)
    reviewer = FakeReviewer()
    repository = FakeKnowledgeRepository([_entry()])

    with pytest.raises(ShadowEvaluationRefused, match="feature flag"):
        asyncio.run(evaluate_caption(
            caption="A test caption.", topic="topic", brand=_brand(),
            repository=repository, reviewer=reviewer,
        ))
    assert reviewer.calls == []
    assert repository.calls == []


def test_shadow_runner_refuses_wrong_or_synthetic_workspace(monkeypatch):
    _enable_staging(monkeypatch)
    reviewer = FakeReviewer()
    repository = FakeKnowledgeRepository([_entry()])
    for workspace in ("ws-e6eb6528d5b9", "default", "platform", "unbound", None):
        with pytest.raises(ShadowEvaluationRefused, match="canonical KDO staging"):
            asyncio.run(evaluate_caption(
                caption="A test caption.", topic="topic",
                brand=_brand(workspace), repository=repository, reviewer=reviewer,
            ))
    assert reviewer.calls == []
    assert repository.calls == []


def test_shadow_runner_requires_persisted_brand_id(monkeypatch):
    _enable_staging(monkeypatch)
    repository = FakeKnowledgeRepository([_entry()])
    with pytest.raises(ShadowEvaluationRefused, match="persisted brand ID"):
        asyncio.run(resolve_staging_evidence(
            query="canary", brand=_brand(brand_id=""), repository=repository,
        ))
    assert repository.calls == []


def test_shadow_runner_rejects_unscoped_repository_results(monkeypatch):
    _enable_staging(monkeypatch)

    class UnsafeRepository(KnowledgeRepository):
        def __init__(self):
            super().__init__()
            self.supabase = SimpleNamespace(supabase_url="https://kkzshurcmcptleyqjgzo.supabase.co")

        def find_by_brand(self, brand_id, workspace_id):
            return [_entry("forged", workspace_id="ws-e6eb6528d5b9")]

    with pytest.raises(ShadowEvaluationRefused, match="provenance"):
        asyncio.run(resolve_staging_evidence(
            query="canary", brand=_brand(), repository=UnsafeRepository(),
        ))


def test_shadow_runner_requires_matching_knowledge_evidence(monkeypatch):
    _enable_staging(monkeypatch)
    repository = FakeKnowledgeRepository([])
    reviewer = FakeReviewer()
    with pytest.raises(ShadowEvaluationRefused, match="no matching knowledge evidence"):
        asyncio.run(evaluate_caption(
            caption="A test caption.", topic="unknown topic", brand=_brand(),
            repository=repository, reviewer=reviewer,
        ))
    assert reviewer.calls == []


def test_shadow_runner_bounds_caption_and_batch_size(monkeypatch):
    _enable_staging(monkeypatch)
    reviewer = FakeReviewer()
    repository = FakeKnowledgeRepository([_entry()])
    with pytest.raises(ShadowEvaluationRefused, match="1–5000"):
        asyncio.run(evaluate_caption(
            caption="x" * 5001, topic="topic", brand=_brand(),
            repository=repository, reviewer=reviewer,
        ))
    with pytest.raises(ShadowEvaluationRefused, match="1–5"):
        asyncio.run(evaluate_batch(
            cases=[{}] * 6, brand=_brand(), repository=repository, reviewer=reviewer,
        ))
    assert reviewer.calls == []
    assert repository.calls == []


def test_shadow_runner_evaluates_batch_sequentially(monkeypatch):
    _enable_staging(monkeypatch)
    reviewer = FakeReviewer()
    repository = FakeKnowledgeRepository([_entry()])
    results = asyncio.run(evaluate_batch(
        cases=[
            {"caption": "Caption A", "topic": "canary"},
            {"caption": "Caption B", "topic": "rollout"},
        ],
        brand=_brand(),
        repository=repository,
        reviewer=reviewer,
    ))

    assert len(results) == 2
    assert [call["caption"] for call in reviewer.calls] == ["Canary rollout A", "Canary rollout B"]


def test_shadow_runner_refuses_production_database_client(monkeypatch):
    _enable_staging(monkeypatch)
    repository = FakeKnowledgeRepository([_entry()])
    repository.supabase.supabase_url = "https://kfgutijhrywnxpsjjacd.supabase.co"
    reviewer = FakeReviewer()

    with pytest.raises(ShadowEvaluationRefused, match="staging database only"):
        asyncio.run(evaluate_caption(
            caption="Canary rollouts limit initial exposure.",
            topic="canary rollout",
            brand=_brand(),
            repository=repository,
            reviewer=reviewer,
        ))
    assert reviewer.calls == []
