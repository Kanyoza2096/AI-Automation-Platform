import asyncio
from types import SimpleNamespace

import pytest

from services.content_semantic_shadow_runner import (
    ShadowEvaluationRefused,
    evaluate_batch,
    evaluate_caption,
)


def _brand(workspace_id="kdo-engineering-staging"):
    return SimpleNamespace(workspace_id=workspace_id)


def _evidence():
    return [{"source_id": "staging-kb-1", "text": "A real staging evidence passage."}]


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


def test_shadow_runner_invokes_reviewer_directly_and_never_publishes(monkeypatch):
    _enable_staging(monkeypatch)
    reviewer = FakeReviewer()
    result = asyncio.run(evaluate_caption(
        caption="A test caption.",
        topic="test topic",
        evidence=_evidence(),
        brand=_brand(),
        reviewer=reviewer,
    ))

    assert len(reviewer.calls) == 1
    assert result["evaluation_type"] == "staging_semantic_shadow"
    assert result["workspace_id"] == "kdo-engineering-staging"
    assert result["published"] is False
    assert result["persisted"] is False
    assert result["result"]["status"] == "needs_review"


def test_shadow_runner_refuses_non_staging_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", "true")
    reviewer = FakeReviewer()

    with pytest.raises(ShadowEvaluationRefused, match="staging-only"):
        asyncio.run(evaluate_caption(
            caption="A test caption.", topic="topic", evidence=_evidence(),
            brand=_brand(), reviewer=reviewer,
        ))
    assert reviewer.calls == []


def test_shadow_runner_requires_explicit_feature_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", raising=False)
    reviewer = FakeReviewer()

    with pytest.raises(ShadowEvaluationRefused, match="feature flag"):
        asyncio.run(evaluate_caption(
            caption="A test caption.", topic="topic", evidence=_evidence(),
            brand=_brand(), reviewer=reviewer,
        ))
    assert reviewer.calls == []


def test_shadow_runner_refuses_wrong_or_synthetic_workspace(monkeypatch):
    _enable_staging(monkeypatch)
    reviewer = FakeReviewer()
    for workspace in ("ws-e6eb6528d5b9", "default", "platform", "unbound", None):
        with pytest.raises(ShadowEvaluationRefused, match="canonical KDO staging"):
            asyncio.run(evaluate_caption(
                caption="A test caption.", topic="topic", evidence=_evidence(),
                brand=_brand(workspace), reviewer=reviewer,
            ))
    assert reviewer.calls == []


def test_shadow_runner_requires_actual_traceable_evidence(monkeypatch):
    _enable_staging(monkeypatch)
    reviewer = FakeReviewer()
    for evidence in ([], [{"text": "missing ID"}], [{"source_id": "id", "text": ""}]):
        with pytest.raises(ShadowEvaluationRefused):
            asyncio.run(evaluate_caption(
                caption="A test caption.", topic="topic", evidence=evidence,
                brand=_brand(), reviewer=reviewer,
            ))
    assert reviewer.calls == []


def test_shadow_runner_bounds_caption_and_batch_size(monkeypatch):
    _enable_staging(monkeypatch)
    reviewer = FakeReviewer()
    with pytest.raises(ShadowEvaluationRefused, match="1–5000"):
        asyncio.run(evaluate_caption(
            caption="x" * 5001, topic="topic", evidence=_evidence(),
            brand=_brand(), reviewer=reviewer,
        ))
    with pytest.raises(ShadowEvaluationRefused, match="1–5"):
        asyncio.run(evaluate_batch(
            cases=[{}] * 6, brand=_brand(), reviewer=reviewer,
        ))
    assert reviewer.calls == []


def test_shadow_runner_evaluates_batch_sequentially(monkeypatch):
    _enable_staging(monkeypatch)
    reviewer = FakeReviewer()
    results = asyncio.run(evaluate_batch(
        cases=[
            {"caption": "Caption A", "topic": "A", "evidence": _evidence()},
            {"caption": "Caption B", "topic": "B", "evidence": _evidence()},
        ],
        brand=_brand(),
        reviewer=reviewer,
    ))

    assert len(results) == 2
    assert [call["caption"] for call in reviewer.calls] == ["Caption A", "Caption B"]
