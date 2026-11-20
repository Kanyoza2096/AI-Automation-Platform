import asyncio
from types import SimpleNamespace

import pytest

from services.content_evidence import (
    validate_caption_evidence,
    validate_editorial_specificity,
)
from workflows.base import StepFailure
from workflows.post_workflow import (
    GenerateContentStep,
    KnowledgeContextStep,
    PublishStep,
    ValidateContentStep,
)


CANARY_STYLE_CAPTION = """When deploying a new version of a system, a canary deployment with a 5% traffic exposure can reduce the blast radius of errors by 95%, as seen in a rollout where a faulty database migration was caught before it affected the entire user base.

This approach is often more effective than relying solely on health signals and rollback mechanisms, which can be slow to detect issues or may not account for complex failure scenarios. For instance, a system with a 2-minute health check interval may take up to 4 minutes to detect and roll back a faulty deployment, during which time a significant portion of users may be affected.

In contrast, a canary deployment allows for the detection of issues in real-time, with the option to quickly roll back or adjust the deployment as needed. What is the smallest percentage of traffic exposure you have used in a canary deployment, and how did it affect your ability to detect and respond to errors?"""


MST_STYLE_CAPTION = """When dealing with disjoint sets and minimum spanning trees, a developer will often reach for a greedy algorithm or an exchange-argument proof to verify the correctness of their approach.

A specific example is the use of Kruskal's algorithm to find the minimum spanning tree of a graph, which relies on a greedy strategy to select the smallest available edge that does not form a cycle.

The key to using greedy algorithms effectively is to understand when they can be trusted to produce an optimal solution, and this is where exchange-argument proofs come in. In the context of union-find and connectivity, a developer might use a greedy algorithm to decide which edges to add to the minimum spanning tree. The next step is to consider how these techniques apply to more complex graph problems."""


def _brand():
    return SimpleNamespace(id="brand-test", name="Test Brand", workspace_id="workspace-test")


def test_examples_are_flagged_as_template_heavy():
    assert validate_editorial_specificity(CANARY_STYLE_CAPTION)[0] is False
    assert validate_editorial_specificity(MST_STYLE_CAPTION)[0] is False


def test_specific_editorial_caption_passes_style_lint():
    caption = (
        "Kruskal's algorithm adds the lightest edge that joins two different "
        "components. A disjoint-set structure makes that cycle check efficient. "
        "Which invariant do you test first when implementing it?"
    )
    assert validate_editorial_specificity(caption)[0] is True


def test_caption_evidence_gate_is_temporarily_disabled():
    ok, reason, sources = validate_caption_evidence(
        "A canary rollout reduces incident impact by 95%.",
        [],
    )
    assert ok
    assert "temporarily disabled" in reason
    assert sources == []


def test_reflective_caption_without_evidence_passes_deterministic_claim_gate():
    ok, reason, sources = validate_caption_evidence(
        "Take a moment to encourage someone today. A little kindness can brighten an ordinary day.",
        [],
    )
    assert ok
    assert reason == "no empirical performance claim detected"
    assert sources == []


def test_conceptual_caption_without_evidence_passes_deterministic_claim_gate():
    ok, reason, sources = validate_caption_evidence(
        "Kruskal's algorithm builds a spanning tree by considering edges in ascending weight order.",
        [],
    )
    assert ok
    assert reason == "no empirical performance claim detected"
    assert sources == []


def test_canary_example_passes_while_caption_evidence_gate_is_disabled():
    evidence = [{
        "source_id": "kb-canary-unverified",
        "text": "Canary deployments expose a small portion of traffic before a full rollout.",
        "metadata": {"verified": False},
    }]
    assert validate_caption_evidence(CANARY_STYLE_CAPTION, evidence)[0] is True


def test_metric_claim_passes_while_caption_evidence_gate_is_disabled():
    evidence = [{
        "source_id": "kb-1",
        "text": "A canary deployment exposed 5% of traffic and reduced the blast radius by 95%.",
        "metadata": {"verified": False},
    }]
    assert validate_caption_evidence(
        "A canary deployment exposed 5% of traffic and reduced the blast radius by 95%.",
        evidence,
    )[0] is True


def test_metric_claim_is_not_blocked_by_temporarily_disabled_gate():
    caption = "A canary deployment exposed 5% of traffic and reduced the blast radius by 95%."
    unrelated_evidence = [{
        "source_id": "kb-unrelated-1",
        "text": "The database query improved by 5ms; the 95% figure refers to a separate cache benchmark.",
        "metadata": {"verified": True},
    }]
    assert validate_caption_evidence(caption, unrelated_evidence)[0] is True


def test_metric_claim_with_mismatched_units_passes_while_gate_is_disabled():
    caption = "A canary deployment exposed 5% of traffic and reduced the blast radius by 95%."
    evidence = [{
        "source_id": "kb-units-1",
        "text": "A canary deployment reduced latency by 5ms and improved throughput by 95 requests per second.",
        "metadata": {"verified": True},
    }]
    assert validate_caption_evidence(caption, evidence)[0] is True


def test_conceptual_caption_can_use_retrieved_non_metric_evidence():
    evidence = [{
        "source_id": "kb-kruskal-1",
        "text": "Kruskal's algorithm adds edges in ascending weight order and uses disjoint sets to detect cycles.",
        "metadata": {"verified": False},
    }]
    ok, _, sources = validate_caption_evidence(
        "Kruskal's algorithm uses disjoint sets to avoid adding an edge that creates a cycle.",
        evidence,
    )
    assert ok
    assert sources == []


def test_knowledge_context_fails_closed_when_repository_is_missing():
    step = KnowledgeContextStep(_brand(), knowledge_repo=None)
    with pytest.raises(StepFailure) as exc:
        asyncio.run(step.async_run({"topic": "Kruskal's algorithm"}))
    assert exc.value.code == "knowledge_repository_unavailable"


def test_knowledge_context_allows_empty_verified_evidence_for_claim_aware_review(monkeypatch):
    import services.knowledge_base as knowledge_base

    class EmptyKnowledgeBase:
        def __init__(self, **kwargs):
            pass

        async def initialize(self):
            return None

        async def search(self, topic, top_k=3):
            return []

    monkeypatch.setattr(knowledge_base, "KnowledgeBase", EmptyKnowledgeBase)
    step = KnowledgeContextStep(_brand(), knowledge_repo=object())
    context = {"topic": "Kruskal's algorithm"}
    evidence = asyncio.run(step.async_run(context))
    assert evidence == []
    assert context["knowledge_evidence"] == []
    assert context["knowledge_context"] == ""


def test_knowledge_context_preserves_traceable_source_metadata(monkeypatch):
    import services.knowledge_base as knowledge_base

    entry = SimpleNamespace(
        id="kb-entry-7",
        content="Kruskal's algorithm uses disjoint sets to detect cycles.",
        metadata={"verified": True, "title": "Kruskal's algorithm"},
    )

    class PopulatedKnowledgeBase:
        def __init__(self, **kwargs):
            pass

        async def initialize(self):
            return None

        async def search(self, topic, top_k=3):
            return [(entry, 0.91)]

    monkeypatch.setattr(knowledge_base, "KnowledgeBase", PopulatedKnowledgeBase)
    context = {"topic": "Kruskal's algorithm"}
    step = KnowledgeContextStep(_brand(), knowledge_repo=object())
    evidence = asyncio.run(step.async_run(context))
    assert evidence[0]["source_id"] == "kb-entry-7"
    assert evidence[0]["metadata"]["verified"] is True
    assert "Kruskal's algorithm" in context["knowledge_context"]
    assert "[source_id=kb-entry-7;verified=true]" in context["knowledge_context"]
    assert context["knowledge_evidence"] == evidence


def test_needs_review_validation_status_does_not_proceed():
    class ReviewRequiredValidator:
        async def validate(self, **kwargs):
            return SimpleNamespace(
                is_approved=False,
                is_rejected=False,
                status=SimpleNamespace(value="needs_review"),
                error_messages=[],
                warnings_count=0,
                to_dict=lambda: {"status": "needs_review"},
            )

    step = ValidateContentStep(validator=ReviewRequiredValidator())
    context = {
        "caption": "A sufficiently long caption for a content validation test.",
        "topic": "Testing",
        "content_result": {},
        "brand": _brand(),
    }
    with pytest.raises(Exception, match="did not approve"):
        asyncio.run(step.async_run(context))


def test_publish_requires_both_validation_and_evidence_approval():
    step = PublishStep(registry=None, brand=_brand())
    with pytest.raises(StepFailure) as validation_exc:
        asyncio.run(step.async_run({"caption": "test"}))
    assert validation_exc.value.code == "publish_validation_not_approved"

    context = {
        "caption": "test",
        "validation_result": SimpleNamespace(is_approved=True),
    }
    with pytest.raises(StepFailure) as evidence_exc:
        asyncio.run(step.async_run(context))
    assert evidence_exc.value.code == "publish_evidence_not_approved"

    context["evidence_validation"] = {"ok": True}
    with pytest.raises(StepFailure) as semantic_exc:
        asyncio.run(step.async_run(context))
    assert semantic_exc.value.code == "publish_semantic_review_not_approved"


def _approved_policy_validator():
    return SimpleNamespace(
        is_approved=True,
        is_rejected=False,
        status=SimpleNamespace(value="approved"),
        error_messages=[],
        warnings_count=0,
        to_dict=lambda: {"status": "approved", "approved": True},
    )


def test_validate_content_step_blocks_when_semantic_review_needs_review():
    class SemanticReviewer:
        async def review(self, **kwargs):
            assert kwargs["topic"] == "Kruskal's algorithm"
            return {
                "approved": False,
                "status": "needs_review",
                "reason": "one claim is uncertain",
                "request_id": "review-123",
            }

    context = {
        "caption": "A concise engineering post with a factual claim.",
        "topic": "Kruskal's algorithm",
        "content_result": {},
        "brand": _brand(),
        "knowledge_evidence": [{
            "source_id": "kb-1",
            "text": "Kruskal's algorithm uses disjoint sets.",
            "metadata": {},
        }],
    }

    class AsyncPolicyValidator:
        async def validate(self, **kwargs):
            return _approved_policy_validator()

    step = ValidateContentStep(
        validator=AsyncPolicyValidator(),
        semantic_reviewer=SemanticReviewer(),
    )
    with pytest.raises(Exception, match="Independent semantic review did not approve"):
        asyncio.run(step.async_run(context))
    assert context["semantic_review"]["approved"] is False


def test_validate_content_step_records_independent_semantic_approval():
    class AsyncPolicyValidator:
        async def validate(self, **kwargs):
            return _approved_policy_validator()

    class SemanticReviewer:
        async def review(self, **kwargs):
            return {
                "approved": True,
                "status": "approved",
                "reason": "all claims supported",
                "claims": [{"claim": "test", "support_status": "supported"}],
                "supporting_source_ids": ["kb-1"],
                "scores": {
                    "brand_relevance": 0.9,
                    "originality": 0.8,
                    "audience_value": 0.8,
                    "genericity": 0.1,
                },
                "request_id": "review-124",
            }

    step = ValidateContentStep(
        validator=AsyncPolicyValidator(),
        semantic_reviewer=SemanticReviewer(),
    )
    context = {
        "caption": "A concise engineering post with a factual claim.",
        "topic": "Kruskal's algorithm",
        "content_result": {},
        "brand": _brand(),
        "knowledge_evidence": [{
            "source_id": "kb-1",
            "text": "Kruskal's algorithm uses disjoint sets.",
            "metadata": {},
        }],
    }

    result = asyncio.run(step.async_run(context))
    assert result["semantic_review"]["approved"] is True
    assert result["semantic_review"]["claims"][0]["support_status"] == "supported"
    assert result["semantic_review"]["supporting_source_ids"] == ["kb-1"]
    assert context["semantic_review"]["status"] == "approved"



def test_prompt_builder_failure_does_not_fall_back_to_internal_prompt(monkeypatch):
    import services.knowledge_base as knowledge_base

    async def no_themes(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(knowledge_base, "aget_brand_themes", no_themes)

    class BrokenPromptBuilder:
        def build_post_prompt(self, **_kwargs):
            raise RuntimeError("simulated prompt assembly failure")

    class FakeContentEngine:
        called = False

        async def generate_post(self, **_kwargs):
            self.called = True
            return {"caption": "This should never be generated."}

    engine = FakeContentEngine()
    step = GenerateContentStep(
        content_engine=engine,
        brand=_brand(),
        ai_profile=None,
        prompt_builder=BrokenPromptBuilder(),
    )

    with pytest.raises(Exception, match="Prompt assembly failed"):
        asyncio.run(step.async_run({
            "topic": "Kruskal's algorithm",
            "knowledge_context": "Kruskal's algorithm uses disjoint sets.",
            "knowledge_evidence": [{
                "source_id": "kb-1",
                "text": "Kruskal's algorithm uses disjoint sets.",
                "metadata": {},
            }],
        }))

    assert engine.called is False


def test_missing_prompt_builder_blocks_generation():
    class FakeContentEngine:
        called = False

        async def generate_post(self, **_kwargs):
            self.called = True
            return {"caption": "This should never be generated."}

    engine = FakeContentEngine()
    step = GenerateContentStep(
        content_engine=engine,
        brand=_brand(),
        ai_profile=None,
        prompt_builder=None,
    )

    with pytest.raises(Exception, match="PromptBuilder is unavailable"):
        asyncio.run(step.async_run({
            "topic": "Kruskal's algorithm",
            "knowledge_context": "Kruskal's algorithm uses disjoint sets.",
            "knowledge_evidence": [{
                "source_id": "kb-1",
                "text": "Kruskal's algorithm uses disjoint sets.",
                "metadata": {},
            }],
        }))

    assert engine.called is False



def test_validate_content_step_rejects_inconsistent_semantic_approval():
    class AsyncPolicyValidator:
        async def validate(self, **kwargs):
            return _approved_policy_validator()

    class InconsistentReviewer:
        async def review(self, **kwargs):
            return {
                "approved": True,
                "status": "needs_review",
                "reason": "inconsistent result",
            }

    step = ValidateContentStep(
        validator=AsyncPolicyValidator(),
        semantic_reviewer=InconsistentReviewer(),
    )
    context = {
        "caption": "A concise engineering post with a factual claim.",
        "topic": "Kruskal's algorithm",
        "content_result": {},
        "brand": _brand(),
        "knowledge_evidence": [{
            "source_id": "kb-1",
            "text": "Kruskal's algorithm uses disjoint sets.",
            "metadata": {},
        }],
    }

    with pytest.raises(Exception, match="Independent semantic review did not approve"):
        asyncio.run(step.async_run(context))


def test_knowledge_context_marks_unverified_sources_for_generation(monkeypatch):
    import services.knowledge_base as knowledge_base

    entry = SimpleNamespace(
        id="kb-unverified-9",
        content="A general conceptual reference without verification.",
        metadata={"source_type": "imported"},
    )

    class UnverifiedKnowledgeBase:
        def __init__(self, **kwargs):
            pass

        async def initialize(self):
            return None

        async def search(self, topic, top_k=3):
            return [(entry, 0.88)]

    monkeypatch.setattr(knowledge_base, "KnowledgeBase", UnverifiedKnowledgeBase)
    context = {"topic": "A conceptual topic"}
    step = KnowledgeContextStep(_brand(), knowledge_repo=object())

    evidence = asyncio.run(step.async_run(context))

    assert evidence == []
    assert context["knowledge_context"] == ""
    assert context["knowledge_evidence"] == []

 
def test_knowledge_prompt_separates_verified_and_unverified_trust_levels():
    from services.prompt_builder import build_knowledge_block

    block = build_knowledge_block(
        "[source_id=kb-verified;verified=true]\nA verified source statement.\n"
        "[source_id=kb-imported;verified=false]\nAn imported reference statement."
    )
    assert "Explicitly verified passages: 1" in block
    assert "Unverified or verification-unspecified passages: 1" in block
    assert "source ID establishes traceability, not trust status" in block
    assert "must not support empirical, numerical, performance, outcome" in block
    assert "\\\\n" not in block
